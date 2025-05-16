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
ANALYSIS_INTERVAL = int(os.getenv('ANALYSIS_INTERVAL', '10'))  # Default to 10 seconds if not specified

# Optional MQTT configuration
MQTT_BROKER_URL = os.getenv('MQTT_BROKER_URL')
MQTT_TOPIC = os.getenv('MQTT_TOPIC')

# Track the last time a failure was detected
last_failure_time = None

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
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        logger.error("Failed to open RTSP stream")
        raise Exception("Failed to open RTSP stream")
    
    ret, frame = cap.read()
    cap.release()
    
    if not ret:
        logger.error("Failed to capture frame")
        raise Exception("Failed to capture frame")
    
    if VERBOSE_LOGGING:
        logger.info("Frame captured successfully")
    return frame

def encode_image(frame):
    """Encode image to base64 in JPEG format."""
    if VERBOSE_LOGGING:
        logger.info("Encoding image to base64 in JPEG format")
    _, buffer = cv2.imencode('.jpg', frame)
    return base64.b64encode(buffer).decode('utf-8')

def analyze_image_with_bedrock(image_base64):
    """Send image to AWS Bedrock for analysis."""
    global bedrock
    if VERBOSE_LOGGING:
        logger.info("Sending image to Bedrock for analysis")
    
    prompt = {
        "schemaVersion": "messages-v1",
        "system": [
            {
                "text": "You are a precise 3D printing quality inspector. You analyze images of active 3D prints and determine if a print failure is occurring."
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "image": {
                            "format": "jpeg",
                            "source": {
                                "bytes": image_base64
                            }
                        }
                    },
                    {
                        "text": "Based on this image of a 3D printer in progress, determine if the print has failed. A common sign of failure is loose or tangled filament (known as 'spaghetti'). Respond only with a JSON object containing one key: 'print_failed' with a boolean value (true or false)."
                    }
                ]
            }
        ],
        "inferenceConfig": {
            "maxTokens": 500,
            "temperature": 0,
            "topP": 1,
            "topK": 1
        }
    }
    
    try:
        response = bedrock.invoke_model(
            modelId=INFERENCE_PROFILE_ARN,
            body=json.dumps(prompt)
        )
        result = json.loads(response['body'].read())
        if VERBOSE_LOGGING:
            logger.info("Received analysis from Bedrock")
        return result
    except bedrock.exceptions.ExpiredTokenException:
        logger.warning("AWS credentials expired, reloading...")
        bedrock = get_aws_session()
        # Retry the request with new credentials
        response = bedrock.invoke_model(
            modelId=INFERENCE_PROFILE_ARN,
            body=json.dumps(prompt)
        )
        result = json.loads(response['body'].read())
        if VERBOSE_LOGGING:
            logger.info("Received analysis from Bedrock after credential reload")
        return result

def send_to_discord(image_path, analysis_result):
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
        parsed_content = json.loads(content_text)
        is_print_failure = parsed_content.get('print_failed', False)

        # Create embed for Discord message
        embed = {
            "title": "⚠️ CRITICAL: Print Failure Detected" if is_print_failure else "ℹ️ Print Status: Normal",
            "description": "Please verify in person or inspect the image above." if is_print_failure else "Print appears to be proceeding normally.",
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

def process_frame():
    """Process a single frame."""
    try:
        # Capture frame
        frame = capture_frame(RTSP_URL)
        
        # Save frame as temporary image file
        temp_image_path = '/tmp/analyzed_frame.jpg'
        cv2.imwrite(temp_image_path, frame)
        
        # Encode image
        image_base64 = encode_image(frame)
        
        # Analyze with Bedrock
        analysis_result = analyze_image_with_bedrock(image_base64)
        
        # Check if a print failure was detected
        content_text = analysis_result.get('output', {}).get('message', {}).get('content', [{}])[0].get('text', '{}')
        parsed_content = json.loads(content_text)
        print_failed = parsed_content.get('print_failed')

        # Determine description
        if print_failed is True:
            description = "Print failure was detected in the image."
        elif print_failed is False:
            description = "No print failure was detected in the image."
        else:
            description = "Could not determine if a print failure was detected."

        # Publish status to MQTT if configured
        publish_status(print_failed, description)

        # Only send a notification if a print failure is detected and it's been more than 15 minutes since the last failure
        global last_failure_time
        if print_failed and (last_failure_time is None or datetime.now() - last_failure_time > timedelta(minutes=15)):
            if DISCORD_WEBHOOK_URL:
                if send_to_discord(temp_image_path, analysis_result):
                    if VERBOSE_LOGGING:
                        logger.info(f"Successfully processed and sent analysis at {datetime.now()}")
                    last_failure_time = datetime.now()
                else:
                    logger.error(f"Failed to send to Discord at {datetime.now()}")
            else:
                if VERBOSE_LOGGING:
                    logger.info(f"Print failure detected at {datetime.now()}, Discord notifications disabled")
                last_failure_time = datetime.now()
        else:
            if print_failed:
                logger.info(f"Print failure detected, but notification suppressed due to recent failure at {datetime.now()}")
            else:
                logger.info(f"No print failure detected at {datetime.now()}")
        
        # Clean up temporary image file
        try:
            os.remove(temp_image_path)
        except Exception as e:
            logger.warning(f"Failed to remove temporary image file: {e}")
        
    except Exception as e:
        logger.error(f"Error occurred: {str(e)}", exc_info=True)

def main():
    if TEST_MODE:
        logger.info("Running in test mode - waiting for manual trigger")
        # Keep the container running but don't process anything
        while True:
            time.sleep(3600)  # Sleep for an hour
    else:
        logger.info(f"Running in continuous mode - processing frames every {ANALYSIS_INTERVAL} seconds")
        while True:
            process_frame()
            time.sleep(ANALYSIS_INTERVAL)

if __name__ == "__main__":
    main() 