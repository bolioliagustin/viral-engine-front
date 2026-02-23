"""
Storage service using Cloudflare R2 (S3 compatible)
Handles uploading clips and generating public URLs
"""
import os
import boto3
from botocore.exceptions import ClientError
from pathlib import Path

# R2 Configuration from env
R2_ACCOUNT_ID = os.getenv('R2_ACCOUNT_ID')
R2_ACCESS_KEY_ID = os.getenv('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.getenv('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = os.getenv('R2_BUCKET_NAME')
R2_PUBLIC_URL = os.getenv('R2_PUBLIC_URL')

def get_s3_client():
    """Create authenticated boto3 client for R2"""
    if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY]):
        print("❌ Missing R2 credentials in environment")
        return None
        
    try:
        return boto3.client(
            's3',
            endpoint_url=f'https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name='auto' # Must be one of: wnam, enam, weur, eeur, apac, auto
        )
    except Exception as e:
        print(f"❌ Failed to create R2 client: {e}")
        return None

def upload_file(file_path: str, object_name: str, content_type: str = 'video/mp4') -> str:
    """
    Upload a file to R2 bucket and return its public URL
    
    Args:
        file_path: Local path to file
        object_name: Destination path in bucket (e.g. 'job-id/clip.mp4')
        content_type: MIME type
        
    Returns:
        Public URL of the uploaded file or None if failed
    """
    s3 = get_s3_client()
    if not s3:
        return None
        
    if not R2_BUCKET_NAME:
        print("❌ R2_BUCKET_NAME not set")
        return None

    try:
        # Upload file
        with open(file_path, "rb") as f:
            s3.upload_fileobj(
                f, 
                R2_BUCKET_NAME, 
                object_name,
                ExtraArgs={'ContentType': content_type}
            )
            
        # Construct public URL
        # If using R2.dev subdomain or custom domain
        if R2_PUBLIC_URL:
             # Remove trailing slash if present
            base_url = R2_PUBLIC_URL.rstrip('/')
            # Ensure object name doesn't start with slash
            clean_object_name = object_name.lstrip('/')
            return f"{base_url}/{clean_object_name}"
            
        else:
            # Fallback (not recommended for public access w/o custom domain setup)
            return f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com/{R2_BUCKET_NAME}/{object_name}"

    except ClientError as e:
        print(f"❌ R2 Upload Error: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error uploading to R2: {e}")
        return None
