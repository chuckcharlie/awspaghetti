#!/bin/bash

# Check if profile name is provided
if [ $# -eq 0 ]; then
    echo "Error: No profile name provided"
    echo "Usage: $0 <profile-name>"
    echo "Example: $0 chuckcharlie"
    exit 1
fi

PROFILE="$1"

# OUTPUT DIRECTORY
OUTPUT_DIR="$HOME/aws-temp-creds"

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

# Set and export default AWS region explicitly
REGION="us-west-2"
export AWS_DEFAULT_REGION="$REGION"

# Function to fetch and write credentials for a profile
fetch_and_write_creds() {
    local PROFILE="$1"
    local ENV_FILE="$OUTPUT_DIR/${PROFILE}-env"
    local AWS_CREDS_FILE="$OUTPUT_DIR/${PROFILE}-credentials"
    
    echo "🔐 Fetching temporary credentials for profile '$PROFILE' using aws-vault and GPG via pass..."
    
    # Fetch temporary credentials using aws-vault + pass
    ENV_OUTPUT=$(aws-vault exec "$PROFILE" --backend=pass -- env | grep ^AWS_)
    
    # Check if credentials were successfully fetched
    if [ -z "$ENV_OUTPUT" ]; then
        echo "❌ Error: Failed to fetch credentials for profile '$PROFILE'"
        exit 1
    fi
    
    # Parse and export environment variables
    eval "$ENV_OUTPUT"
    
    # Write to .env-style file (for sourcing or Docker)
    cat > "$ENV_FILE" <<EOF
# Profile: $PROFILE
AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY
AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN
AWS_DEFAULT_REGION=$REGION
EOF
    
    # Write to AWS credentials file format
    cat > "$AWS_CREDS_FILE" <<EOF
[$PROFILE]
aws_access_key_id = $AWS_ACCESS_KEY_ID
aws_secret_access_key = $AWS_SECRET_ACCESS_KEY
aws_session_token = $AWS_SESSION_TOKEN
region = $REGION
EOF
    
    echo "✅ Temp credentials for profile '$PROFILE' written to:"
    echo "- $ENV_FILE"
    echo "- $AWS_CREDS_FILE"
}

# Process the specified profile
fetch_and_write_creds "$PROFILE" 