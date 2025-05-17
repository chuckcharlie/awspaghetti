# 3D Printer Failure Detection

This application monitors a 3D printer using an RTSP camera feed and AWS Bedrock to detect print failures. It uses computer vision and AI to analyze the printer's status and sends notifications when failures are detected.

## Features

- Real-time monitoring of 3D printer via RTSP camera feed
- AI-powered failure detection using AWS Bedrock
- Discord notifications for critical failures
- MQTT status updates
- Robust error handling and automatic recovery
- Configurable analysis intervals
- Test mode for manual triggering

## How It Works

1. **Frame Capture**: The application captures frames from the RTSP camera feed at configurable intervals.

2. **AI Analysis**: Each frame is analyzed using AWS Bedrock to detect potential print failures. The AI model looks for signs of failure such as loose or tangled filament (known as "spaghetti").

3. **Verification Process**: When a potential failure is detected, the system performs additional verifications:
   - Immediately captures 4 fresh frames from the camera
   - Each frame is analyzed independently
   - Frames are captured 2 seconds apart
   - A failure is only confirmed if 2 or more of these verifications also detect a failure

4. **Notifications**: If a failure is confirmed:
   - A critical alert is sent to Discord with the captured image
   - The system implements a 15-minute cooldown between notifications
   - MQTT status updates are sent (if configured)

5. **Error Handling**: The system includes robust error handling:
   - Automatic retry for AWS Bedrock throttling with exponential backoff
   - Automatic credential refresh when AWS tokens expire
   - Graceful handling of camera feed interruptions
   - Automatic recovery from consecutive errors

## Configuration

The application is configured through environment variables:

- `RTSP_URL`: URL of the RTSP camera feed
- `DISCORD_WEBHOOK_URL`: Discord webhook URL for notifications
- `AWS_REGION`: AWS region (default: us-west-2)
- `AWS_ROLE_ARN`: ARN of the AWS role to assume
- `INFERENCE_PROFILE_ARN`: ARN of the Bedrock inference profile
- `TEST_MODE`: Enable test mode (true/false)
- `VERBOSE_LOGGING`: Enable verbose logging (true/false)
- `APP_AWS_PROFILE`: AWS profile to use (default: default)
- `ANALYSIS_INTERVAL`: Interval between analyses in seconds (default: 10)
- `MQTT_BROKER_URL`: URL of the MQTT broker (optional)
- `MQTT_TOPIC`: MQTT topic for status updates (optional)

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
3. **Credential Expiration**: Automatically refreshes AWS credentials when they expire
4. **Camera Feed**: Implements timeout and retry logic for frame capture

## Logging

The application provides detailed logging:
- Timestamp for each event
- Success/failure of each operation
- Detailed error messages
- Optional verbose logging for debugging

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

## Features

- Captures frames from RTSP stream every 10 seconds
- Analyzes images using AWS Bedrock
- Sends formatted results to Discord webhook
- Automatic error handling and retries
- Containerized for easy deployment

## Configuration

The application can be configured using environment variables in the `docker-compose.yml` file:

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `RTSP_URL` | URL of the RTSP stream to analyze | Yes | - |
| `DISCORD_WEBHOOK_URL` | Discord webhook URL for notifications | No | - |
| `AWS_REGION` | AWS region for Bedrock service | No | us-west-2 |
| `AWS_ROLE_ARN` | ARN of the AWS role to assume | Yes | - |
| `INFERENCE_PROFILE_ARN` | ARN of the Bedrock inference profile | Yes | - |
| `TEST_MODE` | Enable test mode (processes single frame) | No | false |
| `VERBOSE_LOGGING` | Enable verbose logging | No | false |
| `APP_AWS_PROFILE` | AWS profile name to use for credentials | No | default |
| `ANALYSIS_INTERVAL` | Interval between frame analysis in seconds | No | 10 |
| `MQTT_BROKER_URL` | URL of the MQTT broker | No | - |
| `MQTT_TOPIC` | MQTT topic for status updates | No | - |

### Optional Features

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

The status is published every time a frame is analyzed, regardless of whether a Discord notification is sent. This allows other systems to monitor the print status in real-time.

### Volume Mounting

The AWS credentials file is mounted into the Docker container as a read-only file. This is done by specifying the path to the credentials file on the host and the path inside the container where it will be mounted. The `:ro` suffix ensures that the file is mounted as read-only.

### Cost Optimization

The `ANALYSIS_INTERVAL` variable can be used to optimize AWS costs. For example:
- Setting `ANALYSIS_INTERVAL=30` will reduce AWS Bedrock API calls by 66%
- Setting `ANALYSIS_INTERVAL=60` will reduce AWS Bedrock API calls by 83%

Choose an interval that balances your need for timely failure detection with your AWS cost requirements.

## Test Mode

When `TEST_MODE` is set to `true`, the application will not automatically process frames. Instead, it will wait for a manual trigger. You can trigger the workflow manually using the following one-liner:

```bash
docker exec -it rtsp-bedrock-discord python -c "from app import process_frame; process_frame()"
```

## Verbose Logging

Set `VERBOSE_LOGGING` to `true` in the `docker-compose.yml` file to enable detailed logging. This will output additional information about the application's operations, such as frame capture, image encoding, and analysis results.

## Generating AWS Credentials with write-temp-creds.sh

You can generate the credentials file using the provided `write-temp-creds.sh` script. This script fetches temporary credentials for a given AWS profile (using tools like `aws-vault` and `pass`) and writes them to the specified output directory in the required format.

### Usage

```bash
./write-temp-creds.sh <profile-name>
```

This will create or update your credentials file with a section for the specified profile. You can then mount this file into the container and select the profile using the `APP_AWS_PROFILE` environment variable as described above.

For more information on `aws-vault`, visit the [official documentation](https://github.com/99designs/aws-vault).

## AWS Credentials and Profile Selection

This application uses AWS credentials from a mounted credentials file (e.g., `/creds/credentials`). You can specify multiple profiles in this file, similar to the standard AWS credentials format:

```
[default]
aws_access_key_id=AKIAXXXXXXXXXXXXXXXX
aws_secret_access_key=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
aws_session_token=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

[vault-user]
aws_access_key_id=AKIAYYYYYYYYYYYYYYYY
aws_secret_access_key=YYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY
aws_session_token=YYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY
```

(You can generate this file using the `write-temp-creds.sh` script as described above.)

### Selecting a Profile

To select which profile to use, set the `APP_AWS_PROFILE` environment variable in your `docker-compose.yml`:

```yaml
environment:
  - APP_AWS_PROFILE=vault-user
```

**Important:**
- Do **not** set the `AWS_PROFILE` environment variable. If you do, boto3/botocore will try to load the profile from the default AWS credentials/config location (e.g., `~/.aws/credentials`), not from your mounted file. This will cause errors if the profile does not exist there.
- The application reads the `APP_AWS_PROFILE` variable and selects the correct profile from your mounted credentials file internally.

### Summary
- Use `APP_AWS_PROFILE` to select the profile from your mounted credentials file.
- Do **not** use `AWS_PROFILE` in the environment.
- The app will use the credentials from the selected profile for all AWS operations.

## AWS Role Assumption

The application uses AWS credentials to assume a role specified by `AWS_ROLE_ARN`. This role must have the necessary permissions to access AWS Bedrock and perform the required operations.

### Required Permissions

The assumed role should have the following permissions:

- `bedrock:InvokeModel`: To invoke the AWS Bedrock model for image analysis.
- `sts:AssumeRole`: To allow the application to assume the specified role.

### Example Permissions

#### User Permissions

The user running the application should have the following permissions:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "sts:AssumeRole",
            "Resource": "arn:aws:iam::your-account-id:role/your-role-name"
        }
    ]
}
```

#### Role Permissions

The role to be assumed should have the following permissions:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "bedrock:InvokeModel",
            "Resource": "arn:aws:bedrock:us-west-2:your-account-id:inference-profile/your-profile"
        }
    ]
}
```

#### Trust Policy

The trust policy for the role should allow the user to assume the role:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "AWS": "arn:aws:iam::your-account-id:user/your-user-name"
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
```

### Workflow

1. **User Credentials**: The application uses the AWS credentials of the user running the application to authenticate with AWS.

2. **Role Assumption**: The application assumes the role specified by `AWS_ROLE_ARN` using the AWS Security Token Service (STS). This allows the application to perform actions as if it were the assumed role.

3. **Access AWS Bedrock**: With the assumed role, the application can access AWS Bedrock to analyze images and detect print failures.

For more information on AWS IAM roles and permissions, visit the [AWS IAM documentation](https://docs.aws.amazon.com/IAM/latest/UserGuide/introduction.html). 