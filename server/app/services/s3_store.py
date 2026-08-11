"""S3/MinIO 对象存储适配器(真实 Multipart Upload)。"""

from __future__ import annotations

import hashlib

import boto3


class S3ObjectStore:
    def __init__(self, *, endpoint: str, access_key: str, secret_key: str,
                 bucket: str, region: str = "us-east-1"):
        self.bucket = bucket
        self.client = boto3.client(
            "s3", endpoint_url=endpoint,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            region_name=region,
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def create_multipart_upload(self, key: str) -> str:
        return self.client.create_multipart_upload(
            Bucket=self.bucket, Key=key)["UploadId"]

    def presign_upload_part(self, key: str, s3_upload_id: str, part_no: int,
                            expires: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "upload_part",
            Params={"Bucket": self.bucket, "Key": key,
                    "UploadId": s3_upload_id, "PartNumber": part_no},
            ExpiresIn=expires,
        )

    def complete_multipart_upload(self, key: str, s3_upload_id: str,
                                  parts: list[dict]) -> None:
        """parts: [{"PartNumber": n, "ETag": etag}];ETag 不符抛 ClientError。"""
        self.client.complete_multipart_upload(
            Bucket=self.bucket, Key=key, UploadId=s3_upload_id,
            MultipartUpload={"Parts": sorted(parts, key=lambda p: p["PartNumber"])},
        )

    def abort_multipart_upload(self, key: str, s3_upload_id: str) -> None:
        try:
            self.client.abort_multipart_upload(
                Bucket=self.bucket, Key=key, UploadId=s3_upload_id)
        except Exception:
            pass  # 已完成/已中止:幂等

    def head_size(self, key: str) -> int:
        return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]

    def stream_sha256(self, key: str, chunk_size: int = 1 << 20) -> str:
        """流式计算对象 SHA-256(不整块载入内存)。"""
        h = hashlib.sha256()
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        for chunk in iter(lambda: body.read(chunk_size), b""):
            h.update(chunk)
        return h.hexdigest()

    def delete_object(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
