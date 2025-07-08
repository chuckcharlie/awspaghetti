# 3D Printer Failure Detection

This application monitors a 3D printer using an RTSP camera feed and AWS Bedrock to detect print failures. It uses computer vision and AI to analyze the printer's status and sends notifications when failures are detected.

## Features

- Real-time monitoring of 3D printer via RTSP camera feed
- AI-powered failure detection using AWS Bedrock
- Optional Discord notifications for critical failures
- Optional MQTT status updates
- Robust error handling and automatic recovery
- Configurable analysis intervals
- Test mode for manual triggering

## How It Works

1. **Multi-Image Capture**: The application captures a series of 3 images from the RTSP camera feed at configurable intervals (default: 10 seconds) to show progression over time.

2. **AI Analysis**: When not in cooldown:
   - The series of images is analyzed together using AWS Bedrock to detect potential print failures
   - The AI model looks for signs of failure such as loose or tangled filament (known as "spaghetti") and progression of issues across the time series
   - This multi-image approach provides better context for detecting failures that develop gradually

3. **Verification Process**: When a potential failure is detected:
   - The system immediately captures 4 additional series of 3 images each from the camera
   - Each verification series uses 2-second intervals between images (hardcoded for speed, separate from main analysis timing)
   - A failure is only confirmed if at least 3 out of 4 verifications detect a failure
   - This helps prevent false positives while ensuring timely detection

4. **Cooldown Period**: 
   - After a failure is detected and confirmed, a 15-minute cooldown period begins
   - During this period, no Bedrock analysis or verifications are performed
   - This helps reduce API costs and unnecessary processing
   - The system logs when it's in cooldown and when the cooldown will end
   - During cooldown, the system waits 30 seconds between checks to avoid excessive logging

5. **Notifications**: If a failure is confirmed:
   - The 15-minute cooldown period begins
   - Optional notification methods (if configured):
     - Discord: Sends a critical alert with:
       - A fresh image captured at the time of the alert (not from the analysis series)
       - A detailed explanation of why the failure was detected
       - A request to verify in person
     - MQTT: Publishes status updates to the configured topic

6. **Error Handling**: The system includes robust error handling:
   - Automatic retry for AWS Bedrock throttling with exponential backoff
   - Automatic credential refresh when AWS tokens expire
   - Graceful handling of camera feed interruptions
   - Automatic recovery from consecutive errors

## Configuration

The application is configured through environment variables:

| Variable                | Description                                              | Required | Default      |
|-------------------------|----------------------------------------------------------|----------|--------------|
| `RTSP_URL`              | URL of the RTSP stream to analyze                        | Yes      | -            |
| `DISCORD_WEBHOOK_URL`   | Discord webhook URL for notifications                    | No       | -            |
| `AWS_REGION`            | AWS region for Bedrock service                           | No       | us-west-2    |
| `AWS_ROLE_ARN`          | ARN of the AWS role to assume                            | Yes      | -            |
| `INFERENCE_PROFILE_ARN` | ARN of the Bedrock inference profile                     | Yes      | -            |
| `TEST_MODE`             | Enable test mode (processes single frame)                | No       | false        |
| `VERBOSE_LOGGING`       | Enable verbose logging                                   | No       | false        |
| `APP_AWS_PROFILE`       | AWS profile name to use for credentials                  | No       | default      |
| `IMAGES_PER_SERIES`     | Number of images to capture in each analysis cycle       | No       | 3            |
| `INTERVAL_BETWEEN_IMAGES`| Seconds between image captures in a series              | No       | 10           |
| `MQTT_BROKER_URL`       | URL of the MQTT broker                                   | No       | -            |
| `MQTT_TOPIC`            | MQTT topic for status updates                            | No       | -            |

### AWS Credentials and Role Assumption

The application requires AWS credentials to assume a role that has permissions to access Bedrock. These credentials are provided by mounting an AWS credentials file into the container at `/creds/credentials`. The application reads the credentials for the specified profile (`APP_AWS_PROFILE`, default: `default`) and uses them to assume the role specified by `AWS_ROLE_ARN`.

- **Role Assumption:** The application uses the credentials from the mounted file to call AWS STS and assume the specified role. It then uses the temporary credentials from the assumed role to access AWS Bedrock.
- **Credential Expiry Handling:** If the temporary credentials expire (e.g., due to session timeout), the application automatically reloads the credentials from the mounted file and re-assumes the role, ensuring uninterrupted operation. This works seamlessly with external credential refreshers (such as scripts or tools that update the credentials file).
- **Security:** The credentials file is mounted as read-only, and no credentials are hardcoded in the application or image.

#### Required AWS Permissions

**For the user/credentials that will assume the role:**
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "VisualEditor0",
            "Effect": "Allow",
            "Action": "sts:AssumeRole",
            "Resource": "arn:aws:iam::<account>:role/<role name>"
        }
    ]
}
```

**For the role that will access Bedrock:**
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "bedrock:InvokeModel",
            "Resource": [
                "arn:aws:bedrock:us-west-2:<account-id>:inference-profile/us.amazon.nova-premier-v1",
                "arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-premier-v1:*",
                "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-premier-v1:*",
                "arn:aws:bedrock:us-east-2::foundation-model/amazon.nova-premier-v1:*"
            ]
        }
    ]
}
```

**Note:**
- When using inference profiles, AWS Bedrock may forward requests to the region where the underlying model is hosted, even if your profile is in a different region. This means your IAM policy must allow `bedrock:InvokeModel` on the inference profile ARN **and** on the foundation model ARNs in all regions where the model may run (e.g., `us-west-2`, `us-east-1`, `us-east-2`). Adjust the regions and model IDs as needed for your use case.
- The application uses `bedrock:InvokeModel` (non-streaming) and does not require `bedrock:InvokeModelWithResponseStream` permissions.
- The resource ARNs above are for Nova Premier which is the model I am using. This application has also been tested with Nova Pro, but I am not sure if one is better than the other. Replace them with the appropriate ARNs for your inference profile and models.

#### AWS Credentials File Format

The application expects the credentials file to be in the standard AWS credentials format:

```ini
[profile-name]
aws_access_key_id = YOUR_ACCESS_KEY_ID
aws_secret_access_key = YOUR_SECRET_ACCESS_KEY
aws_session_token = YOUR_SESSION_TOKEN
region = us-west-2
```

**Using AWS Vault for Temporary Credentials:**

This repository includes a script (`Scripts/write-temp-creds.sh`) that can help manage temporary credentials using AWS Vault and GPG via pass. This is useful for automatically refreshing credentials before they expire.

```bash
# Usage
./Scripts/write-temp-creds.sh <profile-name>

# Example
./Scripts/write-temp-creds.sh my-aws-profile
```

The script will:
1. Use AWS Vault to fetch temporary credentials for the specified profile
2. Write them to `~/aws-temp-creds/<profile-name>-credentials` in the correct format
3. Also create an environment file at `~/aws-temp-creds/<profile-name>-env`

You can then mount the credentials file in your docker-compose.yml:
```yaml
volumes:
  - ~/aws-temp-creds/my-aws-profile-credentials:/creds/credentials:ro
```

**Prerequisites for the script:**
- [AWS Vault](https://github.com/99designs/aws-vault) installed and configured
- GPG and pass set up for secure credential storage
- A profile configured in AWS Vault

## Docker Deployment

The application is containerized and can be deployed using Docker. The container requires:

1. AWS credentials mounted at `/creds/credentials`
2. Environment variables configured
3. Network access to:
   - RTSP camera feed
   - AWS Bedrock
   - Discord (if notifications enabled)
   - MQTT broker (if configured)

## Error Recovery

The system implements several recovery mechanisms:

1. **Consecutive Errors**: After 5 consecutive errors, the system waits for 60 seconds before retrying
2. **AWS Throttling**: Implements exponential backoff with jitter for Bedrock API calls
3. **AWS Session Management**: 
   - Automatically reloads credentials from the mounted file when they expire
   - Gracefully handles expired token errors with automatic recovery
   - Retries operations after credential refresh
   - Works seamlessly with external credential update scripts
4. **Camera Feed**: Implements timeout and retry logic for frame capture

## Logging

The application provides detailed logging:
- Timestamp for each event
- Success/failure of each operation
- Detailed error messages
- Optional verbose logging for debugging
- Cooldown period status and end time

## Security

- AWS credentials are managed securely through mounted credentials file
- Role-based access control for AWS Bedrock
- No hardcoded credentials
- Secure handling of webhook URLs and MQTT connections

## Prerequisites

- Docker and Docker Compose installed
- AWS credentials file
- RTSP stream URL
- Discord webhook URL (optional)
- MQTT broker (optional)

## Setup

1. Ensure you have the necessary AWS credentials file and RTSP stream URL.

2. Configure the application using the `docker-compose.yml` file.

## Running the Application

1. Build and start the container:
```bash
docker compose up -d
```

2. View logs:
```bash
docker compose logs -f
```

3. Stop the application:
```bash
docker compose down
```

## Optional Features

#### Discord Integration
To enable Discord notifications, set the `DISCORD_WEBHOOK_URL` environment variable in your `docker-compose.yml`:

```yaml
environment:
  - DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your-webhook-url
```

If this variable is not set, the application will skip sending notifications to Discord.

#### MQTT Integration

When MQTT is configured, the application will publish status updates to the specified topic. Each message is a JSON object with the following structure:

```json
{
    "timestamp": "2024-05-16T17:34:42.123456",
    "print_failed": true,
    "description": "Print failure was detected in the image."
}
```

The status is published every time the main analysis cycle runs, regardless of whether a Discord notification is sent. This allows other systems to monitor the print status in real-time.

### Volume Mounting

The AWS credentials file is mounted into the Docker container as a read-only file. This is done by specifying the path to the credentials file on the host and the path inside the container where it will be mounted. The `:ro` suffix ensures that the file is mounted as read-only.

### Cost Optimization

You can adjust the number of images per series and intervals to optimize AWS costs:
- Setting `IMAGES_PER_SERIES=2` will reduce image processing time by 33%
- Setting `INTERVAL_BETWEEN_IMAGES=5` will reduce the total analysis cycle time by 50%
- Setting `INTERVAL_BETWEEN_IMAGES=15` will reduce AWS Bedrock API calls by 33%

Choose settings that balance your need for timely failure detection with your AWS cost requirements.

## Test Mode

When `TEST_MODE` is set to `true`, the application will not automatically process frames. Instead, it will wait for a manual trigger. You can trigger the workflow manually using the following one-liner:

```bash
docker exec -it rtsp-bedrock-discord python -c "from app import process_frame; process_frame()"
```

## Verbose Logging

Set `VERBOSE_LOGGING` to `true` to enable verbose logging.