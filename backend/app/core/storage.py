import os
import shutil
from typing import Optional, BinaryIO, Union
from backend.app.core.config import settings
from backend.app.core.logging import logger

class BaseStorage:
    def save_file(self, source: Union[str, bytes, BinaryIO], key: str, content_type: Optional[str] = None) -> str:
        raise NotImplementedError

    def get_local_path(self, key: str) -> str:
        raise NotImplementedError

    def get_url(self, key: str) -> str:
        raise NotImplementedError

    def delete_file(self, key: str) -> bool:
        raise NotImplementedError

    def file_exists(self, key: str) -> bool:
        raise NotImplementedError


class LocalStorage(BaseStorage):
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = os.path.abspath(base_dir or os.path.join("data", "storage"))
        os.makedirs(self.base_dir, exist_ok=True)
        logger.info(f"[Storage] Initialized LocalStorage at {self.base_dir}")

    def _resolve_path(self, key: str) -> str:
        # Prevent directory traversal
        clean_key = os.path.normpath(key).lstrip("/\\")
        if clean_key.startswith(".."):
            raise ValueError(f"Invalid storage key: {key}")
        full_path = os.path.join(self.base_dir, clean_key)
        return full_path

    def save_file(self, source: Union[str, bytes, BinaryIO], key: str, content_type: Optional[str] = None) -> str:
        dest_path = self._resolve_path(key)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        if isinstance(source, str):
            if os.path.exists(source):
                shutil.copy2(source, dest_path)
            else:
                raise FileNotFoundError(f"Source file not found: {source}")
        elif isinstance(source, bytes):
            with open(dest_path, "wb") as f:
                f.write(source)
        else:
            # File-like object
            with open(dest_path, "wb") as f:
                shutil.copyfileobj(source, f)

        logger.debug(f"[Storage] Saved key={key} to {dest_path}")
        return key

    def get_local_path(self, key: str) -> str:
        dest_path = self._resolve_path(key)
        return dest_path

    def get_url(self, key: str) -> str:
        clean_key = os.path.normpath(key).lstrip("/\\").replace("\\", "/")
        return f"/api/lab/files/{clean_key}"

    def delete_file(self, key: str) -> bool:
        dest_path = self._resolve_path(key)
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
                logger.info(f"[Storage] Deleted key={key} at {dest_path}")
                return True
            except Exception as e:
                logger.warning(f"[Storage] Failed to delete key={key}: {e}")
                return False
        return False

    def file_exists(self, key: str) -> bool:
        dest_path = self._resolve_path(key)
        return os.path.exists(dest_path)


class S3Storage(BaseStorage):
    def __init__(self, bucket: str, endpoint_url: Optional[str] = None, region: Optional[str] = None):
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.region = region or "us-east-1"
        self.cache_dir = os.path.abspath(os.path.join("data", "storage_cache"))
        os.makedirs(self.cache_dir, exist_ok=True)

        import boto3
        from botocore.config import Config
        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self.region,
            config=Config(signature_version="s3v4")
        )
        logger.info(f"[Storage] Initialized S3Storage for bucket={bucket} endpoint={endpoint_url}")

    def save_file(self, source: Union[str, bytes, BinaryIO], key: str, content_type: Optional[str] = None) -> str:
        clean_key = key.lstrip("/\\")
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type

        if isinstance(source, str):
            self.client.upload_file(source, self.bucket, clean_key, ExtraArgs=extra_args)
        elif isinstance(source, bytes):
            import io
            self.client.upload_fileobj(io.BytesIO(source), self.bucket, clean_key, ExtraArgs=extra_args)
        else:
            self.client.upload_fileobj(source, self.bucket, clean_key, ExtraArgs=extra_args)

        logger.info(f"[Storage] Uploaded key={clean_key} to S3 bucket={self.bucket}")
        return clean_key

    def get_local_path(self, key: str) -> str:
        clean_key = key.lstrip("/\\")
        local_path = os.path.join(self.cache_dir, clean_key)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        if not os.path.exists(local_path):
            logger.info(f"[Storage] Downloading key={clean_key} from S3 to cache={local_path}")
            self.client.download_file(self.bucket, clean_key, local_path)
        return local_path

    def get_url(self, key: str) -> str:
        clean_key = key.lstrip("/\\")
        # Generate presigned download URL valid for 2 hours
        try:
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": clean_key},
                ExpiresIn=7200
            )
            return url
        except Exception as e:
            logger.warning(f"[Storage] Failed to generate presigned URL for {clean_key}: {e}")
            return f"/api/lab/files/{clean_key}"

    def delete_file(self, key: str) -> bool:
        clean_key = key.lstrip("/\\")
        try:
            self.client.delete_object(Bucket=self.bucket, Key=clean_key)
            # Also remove cached copy if present
            local_path = os.path.join(self.cache_dir, clean_key)
            if os.path.exists(local_path):
                os.remove(local_path)
            return True
        except Exception as e:
            logger.warning(f"[Storage] S3 deletion error for {clean_key}: {e}")
            return False

    def file_exists(self, key: str) -> bool:
        clean_key = key.lstrip("/\\")
        try:
            self.client.head_object(Bucket=self.bucket, Key=clean_key)
            return True
        except Exception:
            return False


_storage_instance: Optional[BaseStorage] = None

def get_storage() -> BaseStorage:
    global _storage_instance
    if _storage_instance is None:
        s3_bucket = os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_S3_BUCKET")
        if s3_bucket:
            try:
                endpoint = os.getenv("S3_ENDPOINT_URL")
                region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
                _storage_instance = S3Storage(bucket=s3_bucket, endpoint_url=endpoint, region=region)
            except Exception as e:
                logger.error(f"[Storage] Failed to initialize S3 storage ({e}). Falling back to LocalStorage.")
                _storage_instance = LocalStorage()
        else:
            storage_dir = os.getenv("STORAGE_LOCAL_DIR") or os.path.join("data", "storage")
            _storage_instance = LocalStorage(base_dir=storage_dir)
    return _storage_instance
