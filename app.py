import cv2
import time
import json
import requests
import boto3
import os
import logging
from datetime import datetime, timedelta
import base64
from io import BytesIO
import paho.mqtt.client as mqtt
from urllib.parse import urlparse
from collections import deque
import random
import re

# Suppress OpenCV's H264 warnings
os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'
os.environ['OPENCV_FFMPEG_LOGLEVEL'] = '0'  # Suppress FFMPEG logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configuration
RTSP_URL = os.getenv('RTSP_URL')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')
AWS_REGION = os.getenv('AWS_REGION', 'us-west-2')
AWS_ROLE_ARN = os.getenv('AWS_ROLE_ARN')
INFERENCE_PROFILE_ARN = os.getenv('INFERENCE_PROFILE_ARN')
TEST_MODE = os.getenv('TEST_MODE', 'false').lower() == 'true'
VERBOSE_LOGGING = os.getenv('VERBOSE_LOGGING', 'false').lower() == 'true'
APP_AWS_PROFILE = os.getenv('APP_AWS_PROFILE', 'default')
IMAGES_PER_SERIES = int(os.getenv('IMAGES_PER_SERIES', '3'))  # Number of images to capture in each series
INTERVAL_BETWEEN_IMAGES = int(os.getenv('INTERVAL_BETWEEN_IMAGES', '10'))  # Seconds between image captures

# Optional MQTT configuration
MQTT_BROKER_URL = os.getenv('MQTT_BROKER_URL')
MQTT_TOPIC = os.getenv('MQTT_TOPIC')

# Track the last time a failure was detected
last_failure_time = None

# Track the last 5 analysis results
failure_window = deque(maxlen=5)

# MQTT client setup
mqtt_client = None
if MQTT_BROKER_URL and MQTT_TOPIC:
    try:
        mqtt_client = mqtt.Client()
        parsed_url = urlparse(MQTT_BROKER_URL)
        broker_host = parsed_url.hostname
        broker_port = parsed_url.port or 1883
        
        # Connect to MQTT broker
        mqtt_client.connect(broker_host, broker_port)
        mqtt_client.loop_start()
        logger.info(f"Connected to MQTT broker at {broker_host}:{broker_port}")
    except Exception as e:
        logger.error(f"Failed to connect to MQTT broker: {e}")
        mqtt_client = None

logger.info("Initializing AWS session and assuming role")

def get_aws_session():
    """Initialize AWS session and handle expired tokens."""
    def parse_aws_credentials_file(file_path, profile_name='default'):
        """Parse AWS credentials file and return credentials for specified profile."""
        credentials = {}
        current_profile = None
        
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                    
                if line.startswith('[') and line.endswith(']'):
                    current_profile = line[1:-1]
                    continue
                    
                if current_profile == profile_name:
                    if '=' in line:
                        key, value = line.split('=', 1)
                        credentials[key.strip()] = value.strip()
        
        return credentials

    # Read credentials from mounted file
    creds_path = '/creds/credentials'
    if not os.path.exists(creds_path):
        raise Exception(f"Credentials file not found at {creds_path}")

    # Get credentials for the specified profile
    credentials = parse_aws_credentials_file(creds_path, APP_AWS_PROFILE)

    if not credentials:
        raise Exception(f"No credentials found for profile '{APP_AWS_PROFILE}' in credentials file")

    aws_access_key_id = credentials.get('aws_access_key_id')
    aws_secret_access_key = credentials.get('aws_secret_access_key')
    aws_session_token = credentials.get('aws_session_token')

    if not all([aws_access_key_id, aws_secret_access_key, aws_session_token]):
        raise Exception(f"Missing required AWS credentials for profile '{APP_AWS_PROFILE}' in credentials file")

    # Initialize AWS session with credentials directly
    session = boto3.Session(
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        aws_session_token=aws_session_token,
        region_name=AWS_REGION
    )

    # Assume the bedrock role
    sts_client = session.client('sts')
    assumed_role = sts_client.assume_role(
        RoleArn=AWS_ROLE_ARN,
        RoleSessionName='BedrockSession'
    )

    # Create new session with assumed role credentials
    assumed_session = boto3.Session(
        aws_access_key_id=assumed_role['Credentials']['AccessKeyId'],
        aws_secret_access_key=assumed_role['Credentials']['SecretAccessKey'],
        aws_session_token=assumed_role['Credentials']['SessionToken'],
        region_name=AWS_REGION
    )

    # Initialize AWS Bedrock client with assumed role
    bedrock = assumed_session.client('bedrock-runtime', region_name=AWS_REGION)
    return bedrock

# Initialize the Bedrock client
bedrock = get_aws_session()
logger.info("AWS session initialized successfully")

def capture_frame(rtsp_url):
    """Capture a frame from the RTSP stream."""
    if VERBOSE_LOGGING:
        logger.info(f"Attempting to capture frame from {rtsp_url}")
    
    # Set OpenCV to be more resilient to stream errors
    cap = cv2.VideoCapture(rtsp_url)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimize buffer size to get latest frame
    
    if not cap.isOpened():
        logger.error("Failed to open RTSP stream")
        raise Exception("Failed to open RTSP stream")
    
    try:
        # Try to read frame with timeout
        start_time = time.time()
        timeout = 5  # seconds
        
        while time.time() - start_time < timeout:
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                if VERBOSE_LOGGING:
                    logger.info("Frame captured successfully")
                return frame
            time.sleep(0.1)  # Short delay between attempts
            
        logger.error("Timeout waiting for valid frame from RTSP stream")
        raise Exception("Timeout waiting for valid frame")
        
    except Exception as e:
        logger.error(f"Error capturing frame: {str(e)}")
        raise
    finally:
        cap.release()

def encode_image(frame):
    """Encode image to base64 in JPEG format."""
    if VERBOSE_LOGGING:
        logger.info("Encoding image to base64 in JPEG format")
    _, buffer = cv2.imencode('.jpg', frame)
    return base64.b64encode(buffer).decode('utf-8')

def extract_json_from_bedrock_response(text):
    """
    Extracts the JSON object from a Bedrock response, handling both:
    - New format: triple-backtick-wrapped JSON (with or without 'json' label)
    - Old format: raw JSON string
    Returns a dict, or raises ValueError if parsing fails.
    """
    if not isinstance(text, str):
        raise ValueError("Input to extract_json_from_bedrock_response must be a string")
    # Try to find triple-backtick-wrapped JSON
    match = re.search(r"```(?:json)?\n?(.*?)```", text, re.DOTALL)
    if match:
        json_str = match.group(1).strip()
    else:
        json_str = text.strip()
    return json.loads(json_str)

def verify_failure():
    """Verify a potential failure by analyzing multiple frames in series."""
    logger.info("Starting failure verification process with multi-image analysis...")
    failures = 0
    total_verifications = 4
    VERIFICATION_INTERVAL = 2  # Hardcoded 2 second interval for verification only
    
    for i in range(total_verifications):
        try:
            # Capture images at 2-second intervals for this verification
            image_series = []
            for j in range(IMAGES_PER_SERIES):
                if VERBOSE_LOGGING:
                    logger.info(f"Capturing frame {j+1}/{IMAGES_PER_SERIES} for verification {i+1}/{total_verifications}")
                
                frame = capture_frame(RTSP_URL)
                if frame is None:
                    logger.error(f"Failed to capture frame {j+1} for verification {i+1}")
                    break
                
                image_base64 = encode_image(frame)
                image_series.append(image_base64)
                
                # Wait 2 seconds between captures (except after the last one)
                if j < IMAGES_PER_SERIES - 1:
                    time.sleep(VERIFICATION_INTERVAL)
            
            # If we didn't get all frames, skip this verification
            if len(image_series) != IMAGES_PER_SERIES:
                logger.error(f"Failed to capture all {IMAGES_PER_SERIES} frames for verification {i+1}, skipping")
                continue
            
            # Analyze the series of frames
            if VERBOSE_LOGGING:
                logger.info(f"Starting verification {i+1}/{total_verifications} with Bedrock ({IMAGES_PER_SERIES} images)")
            try:
                analysis_result = analyze_images_with_bedrock(image_series)
            except Exception as e:
                error_msg = str(e)
                if any(token_error in error_msg for token_error in ['ExpiredToken', 'TokenExpired', 'ExpiredTokenException', 'InvalidToken']):
                    logger.warning(f"AWS session error during verification {i+1}, attempting to refresh...")
                    if refresh_aws_session():
                        logger.info("AWS session refreshed, retrying verification...")
                        try:
                            analysis_result = analyze_images_with_bedrock(image_series)
                        except Exception as retry_error:
                            logger.error(f"Failed to retry verification {i+1} after session refresh: {retry_error}")
                            continue
                    else:
                        logger.error(f"Failed to refresh AWS session during verification {i+1}")
                        continue
                else:
                    logger.error(f"Error during verification {i+1}: {error_msg}")
                    continue
            
            # Parse the response
            content_text = analysis_result.get('output', {}).get('message', {}).get('content', [{}])[0].get('text', '{}')
            if VERBOSE_LOGGING:
                logger.info(f"Raw Bedrock response for verification {i+1}: {content_text}")
            parsed_content = extract_json_from_bedrock_response(content_text)
            
            if parsed_content.get('print_failed', False):
                failures += 1
                explanation = parsed_content.get('explanation', 'No explanation provided')
                logger.info(f"Verification {i+1}/{total_verifications}: Failure confirmed - {explanation}")
            else:
                logger.info(f"Verification {i+1}/{total_verifications}: No failure detected")
            
            # Wait 2 seconds between verifications
            if i < total_verifications - 1:  # Don't wait after the last verification
                time.sleep(2)
            
        except Exception as e:
            logger.error(f"Error during verification {i+1}: {str(e)}")
            continue
    
    logger.info(f"Verification complete: {failures}/{total_verifications} failures detected")
    return failures >= 3  # Return True if 3 or more verifications failed

def analyze_images_with_bedrock(image_base64_list):
    """Send multiple images to AWS Bedrock for analysis."""
    global bedrock
    if VERBOSE_LOGGING:
        logger.info(f"Sending {len(image_base64_list)} images to Bedrock for analysis")
    
    # Load and prepare the prompt template
    try:
        with open('prompt.json', 'r') as f:
            prompt_template = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load prompt template: {e}")
        raise
    
    # Replace the image_base64 placeholders
    prompt_str = json.dumps(prompt_template)
    for i, image_base64 in enumerate(image_base64_list, 1):
        placeholder = f'"{{{{image{i}_base64}}}}"'
        prompt_str = prompt_str.replace(placeholder, f'"{image_base64}"')
    
    # Replace the interval_seconds placeholder with the actual configuration value
    prompt_str = prompt_str.replace('{{interval_seconds}}', str(INTERVAL_BETWEEN_IMAGES))
    
    prompt = json.loads(prompt_str)
    
    max_retries = 5
    base_delay = 1  # Start with 1 second
    max_delay = 32  # Maximum delay of 32 seconds
    jitter = 0.1  # 10% jitter
    
    for attempt in range(max_retries):
        try:
            response = bedrock.invoke_model(
                modelId=INFERENCE_PROFILE_ARN,
                body=json.dumps(prompt)
            )
            result = json.loads(response['body'].read())
            if VERBOSE_LOGGING:
                logger.info("Received analysis from Bedrock")
            return result
            
        except bedrock.exceptions.ThrottlingException as e:
            if attempt < max_retries - 1:
                # Calculate delay with exponential backoff and jitter
                delay = min(max_delay, base_delay * (2 ** attempt))
                jitter_amount = delay * jitter
                actual_delay = delay + (random.uniform(-jitter_amount, jitter_amount))
                
                logger.warning(f"Bedrock throttling, attempt {attempt + 1}/{max_retries}. "
                             f"Waiting {actual_delay:.1f} seconds...")
                time.sleep(actual_delay)
                continue
            else:
                logger.error(f"Bedrock throttling after {max_retries} attempts: {str(e)}")
                raise
                
        except Exception as e:
            error_msg = str(e)
            # Check for various expired token error patterns
            if any(token_error in error_msg for token_error in ['ExpiredToken', 'TokenExpired', 'ExpiredTokenException', 'InvalidToken']):
                logger.warning(f"AWS credentials expired: {error_msg}")
                logger.info("Reloading credentials from mounted file...")
                try:
                    bedrock = get_aws_session()  # This will reload fresh credentials from the mounted file
                    logger.info("Successfully reloaded AWS credentials")
                    continue  # Retry the request with fresh credentials
                except Exception as refresh_error:
                    logger.error(f"Failed to reload AWS credentials: {refresh_error}")
                    raise
            else:
                logger.error(f"Unexpected error in Bedrock analysis: {error_msg}")
                raise

def analyze_image_with_bedrock(image_base64):
    """Send single image to AWS Bedrock for analysis (backward compatibility)."""
    return analyze_images_with_bedrock([image_base64])

def send_to_discord(image_path, analysis_result, explanation):
    """Send analysis results to Discord webhook"""
    if not DISCORD_WEBHOOK_URL:
        logger.info("Discord webhook URL not configured, skipping Discord notification")
        return

    try:
        # Parse the analysis result to extract message content
        if isinstance(analysis_result, str):
            result_dict = json.loads(analysis_result)
        else:
            result_dict = analysis_result
            
        # Extract the print_failed status from the analysis result
        content_text = result_dict.get('output', {}).get('message', {}).get('content', [{}])[0].get('text', '{}')
        parsed_content = extract_json_from_bedrock_response(content_text)
        is_print_failure = parsed_content.get('print_failed', False)

        # Create embed for Discord message
        embed = {
            "title": "⚠️ CRITICAL: Print Failure Detected" if is_print_failure else "ℹ️ Print Status: Normal",
            "description": f"Please verify in person or inspect the image above.\n\n**Analysis:** {explanation}" if is_print_failure else "Print appears to be proceeding normally.",
            "color": 0xFF0000 if is_print_failure else 0x00FF00,  # Red for failure, green for success
            "timestamp": datetime.utcnow().isoformat()
        }

        # Prepare the payload
        payload = {
            "content": "⚠️ **CRITICAL: Print Failure Detected**" if is_print_failure else "ℹ️ Print Status: Normal",
            "embeds": [embed]
        }

        # Convert payload to JSON string
        payload_json = json.dumps(payload)

        # Prepare the multipart form data
        files = {
            'file': ('analyzed_frame.jpg', open(image_path, 'rb'), 'image/jpeg')
        }
        data = {
            'payload_json': payload_json
        }

        # Send to Discord
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            files=files,
            data=data
        )
        response.raise_for_status()
        logger.info("Successfully sent analysis results to Discord")
        return True
    except Exception as e:
        logger.error(f"Failed to send to Discord: {str(e)}")
        logger.error(f"Failed to send to Discord at {datetime.now()}")
        return False

def publish_status(print_failed, description):
    """Publish status to MQTT topic if configured."""
    if mqtt_client and MQTT_TOPIC:
        try:
            status = {
                "timestamp": datetime.now().isoformat(),
                "print_failed": print_failed,
                "description": description
            }
            mqtt_client.publish(MQTT_TOPIC, json.dumps(status))
            if VERBOSE_LOGGING:
                logger.info(f"Published status to MQTT topic {MQTT_TOPIC}")
        except Exception as e:
            logger.error(f"Failed to publish to MQTT: {e}")

def refresh_aws_session():
    """Refresh the AWS session by reloading credentials from the mounted file."""
    global bedrock
    logger.info("Refreshing AWS session from mounted credentials file...")
    try:
        bedrock = get_aws_session()
        logger.info("AWS session refreshed successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to refresh AWS session: {e}")
        return False

def process_frame():
    """Process a series of frames captured at intervals."""
    try:
        # Check if we're in cooldown period
        global last_failure_time
        if last_failure_time is not None and datetime.now() - last_failure_time <= timedelta(minutes=15):
            logger.info(f"In cooldown period until {last_failure_time + timedelta(minutes=15)}. Skipping Bedrock analysis.")
            return True  # Indicate we were in cooldown

        # Capture 3 frames at 10-second intervals
        image_series = []
        temp_image_paths = []
        
        for i in range(IMAGES_PER_SERIES):
            if VERBOSE_LOGGING:
                logger.info(f"Capturing frame {i+1}/{IMAGES_PER_SERIES} for analysis")
            
            frame = capture_frame(RTSP_URL)
            if frame is None:
                logger.error(f"Failed to capture frame {i+1} - frame is None")
                return False
            
            # Save frame as temporary image file
            temp_image_path = f'/tmp/analyzed_frame_{i+1}.jpg'
            try:
                cv2.imwrite(temp_image_path, frame)
                temp_image_paths.append(temp_image_path)
            except Exception as e:
                logger.error(f"Failed to save frame {i+1} to temporary file: {e}")
                return False
            
            # Encode image
            try:
                image_base64 = encode_image(frame)
                image_series.append(image_base64)
            except Exception as e:
                logger.error(f"Failed to encode image {i+1}: {e}")
                return False
            
            # Wait between captures (except after the last one)
            if i < IMAGES_PER_SERIES - 1:  # Don't wait after the last frame
                time.sleep(INTERVAL_BETWEEN_IMAGES)
        
        # Initial analysis with Bedrock using all 3 images
        try:
            analysis_result = analyze_images_with_bedrock(image_series)
        except Exception as e:
            error_msg = str(e)
            if any(token_error in error_msg for token_error in ['ExpiredToken', 'TokenExpired', 'ExpiredTokenException', 'InvalidToken']):
                logger.warning("AWS session error during initial analysis, attempting to refresh...")
                if refresh_aws_session():
                    logger.info("AWS session refreshed, retrying initial analysis...")
                    try:
                        analysis_result = analyze_images_with_bedrock(image_series)
                    except Exception as retry_error:
                        logger.error(f"Failed to retry initial analysis after session refresh: {retry_error}")
                        return False
                else:
                    logger.error("Failed to refresh AWS session during initial analysis")
                    return False
            else:
                logger.error(f"Failed to analyze images with Bedrock: {error_msg}")
                return False
        
        # Check if a print failure was detected
        try:
            content_text = analysis_result.get('output', {}).get('message', {}).get('content', [{}])[0].get('text', '{}')
            if VERBOSE_LOGGING:
                logger.info(f"Raw Bedrock response for initial analysis: {content_text}")
            parsed_content = extract_json_from_bedrock_response(content_text)
            print_failed = parsed_content.get('print_failed')
            explanation = parsed_content.get('explanation', 'No explanation provided')
            
            # If initial analysis indicates failure, perform rapid verifications
            if print_failed:
                logger.info(f"Initial analysis indicates failure: {explanation}")
                logger.info("Starting rapid verifications...")
                confirmed_failure = verify_failure()
                if not confirmed_failure:
                    logger.info("Failure not confirmed by verifications")
                    print_failed = False
            else:
                if VERBOSE_LOGGING:
                    logger.info("No failure detected in initial analysis")
            
        except Exception as e:
            logger.error(f"Failed to parse analysis result: {e}")
            return False

        # Determine description
        if print_failed is True:
            description = "Print failure was confirmed by multiple verifications."
        elif print_failed is False:
            description = "No print failure was detected."
        else:
            description = "Could not determine if a print failure was detected."

        # Publish status to MQTT if configured
        try:
            publish_status(print_failed, description)
        except Exception as e:
            logger.error(f"Failed to publish MQTT status: {e}")

        # Only send a notification if failure was confirmed by verifications
        if print_failed:
            if DISCORD_WEBHOOK_URL:
                try:
                    # Capture a fresh image for Discord alert
                    logger.info("Capturing fresh image for Discord alert")
                    fresh_frame = capture_frame(RTSP_URL)
                    if fresh_frame is not None:
                        fresh_image_path = '/tmp/discord_alert_image.jpg'
                        try:
                            cv2.imwrite(fresh_image_path, fresh_frame)
                            if send_to_discord(fresh_image_path, analysis_result, explanation):
                                if VERBOSE_LOGGING:
                                    logger.info(f"Successfully processed and sent analysis at {datetime.now()}")
                                last_failure_time = datetime.now()
                            else:
                                logger.error(f"Failed to send to Discord at {datetime.now()}")
                            # Clean up the fresh image
                            try:
                                os.remove(fresh_image_path)
                            except Exception as e:
                                logger.warning(f"Failed to remove Discord alert image: {e}")
                        except Exception as e:
                            logger.error(f"Failed to save fresh image for Discord: {e}")
                    else:
                        logger.error("Failed to capture fresh image for Discord alert")
                except Exception as e:
                    logger.error(f"Error sending to Discord: {e}")
            else:
                if VERBOSE_LOGGING:
                    logger.info(f"Print failure detected at {datetime.now()}, Discord notifications disabled")
                last_failure_time = datetime.now()
        else:
            logger.info(f"No print failure detected at {datetime.now()}")
        
        # Clean up temporary image files
        for temp_image_path in temp_image_paths:
            try:
                os.remove(temp_image_path)
            except Exception as e:
                logger.warning(f"Failed to remove temporary image file {temp_image_path}: {e}")
        
        return False  # Indicate we were not in cooldown
        
    except Exception as e:
        logger.error(f"Error occurred in process_frame: {str(e)}", exc_info=True)
        raise  # Re-raise the exception to be handled by the main loop

def main():
    if TEST_MODE:
        logger.info("Running in test mode - waiting for manual trigger")
        # Keep the container running but don't process anything
        while True:
            time.sleep(3600)  # Sleep for an hour
    else:
        # Calculate total time for one analysis cycle
        total_analysis_time = (IMAGES_PER_SERIES - 1) * INTERVAL_BETWEEN_IMAGES
        
        logger.info(f"Running in continuous mode - processing {IMAGES_PER_SERIES} images")
        logger.info(f"Each analysis cycle captures {IMAGES_PER_SERIES} images at {INTERVAL_BETWEEN_IMAGES}-second intervals (total: {total_analysis_time}s)")
        logger.info(f"Analysis cycles run continuously with no additional wait time")
        
        consecutive_errors = 0
        max_consecutive_errors = 5
        error_cooldown = 60  # seconds to wait after multiple errors
        
        while True:
            try:
                was_in_cooldown = process_frame()
                consecutive_errors = 0  # Reset error counter on success
                
                # If we were in cooldown, wait before checking again
                if was_in_cooldown:
                    time.sleep(30)  # Wait 30 seconds before checking cooldown again
                # No additional wait time - next cycle starts immediately
            except Exception as e:
                error_msg = str(e)
                consecutive_errors += 1
                logger.error(f"Error in main loop: {error_msg}", exc_info=True)
                
                # Check if this is an AWS session related error
                if any(token_error in error_msg for token_error in ['ExpiredToken', 'TokenExpired', 'ExpiredTokenException', 'InvalidToken']):
                    logger.warning("AWS session error detected, attempting to refresh...")
                    if refresh_aws_session():
                        logger.info("AWS session refreshed, continuing...")
                        consecutive_errors = 0  # Reset error counter after successful refresh
                        continue
                    else:
                        logger.error("Failed to refresh AWS session")
                        # Continue with normal error handling
                
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many consecutive errors ({consecutive_errors}). Waiting {error_cooldown} seconds before retrying...")
                    time.sleep(error_cooldown)
                    consecutive_errors = 0  # Reset after cooldown
                else:
                    # Wait a bit before retrying
                    time.sleep(10)

if __name__ == "__main__":
    main() 