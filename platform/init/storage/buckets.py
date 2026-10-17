import os
import boto3
from botocore.exceptions import ClientError
from botocore.config import Config

client=boto3.client('s3',endpoint_url=os.environ.get('S3_ENDPOINT','http://rustfs:9000'),region_name=os.environ.get('S3_REGION','us-east-1'),
    aws_access_key_id=os.environ['S3_ACCESS_KEY'],aws_secret_access_key=os.environ['S3_SECRET_KEY'],
    config=Config(s3={'addressing_style':'path'}))
for bucket in ('supabase-storage','langfuse'):
    try: client.head_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response['ResponseMetadata']['HTTPStatusCode']!=404: raise
        client.create_bucket(Bucket=bucket)
    print('对象存储已就绪：'+bucket)
