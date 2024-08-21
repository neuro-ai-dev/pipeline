import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from loguru import logger

from pipeline.cloud.schemas import files as files_schemas

router = APIRouter(prefix="/files", tags=["Files"])


@router.get("/download/{path:path}", status_code=status.HTTP_200_OK)
async def read_file(path: str):
    """Download the contents of a file stored on the container."""
    # Check if the path is intended to be absolute
    if path.startswith("/"):
        file_path = Path(path)
    else:
        base_path = Path("/")
        file_path = base_path / path

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
)
async def file_upload(
    pfile: UploadFile,
):
    """Upload a file to the container. Returns the path of the file on the container."""
    data = await pfile.read()
    pfile_uid = str(uuid4())
    name = getattr(pfile, "filename")
    name = Path(name).name

    logger.info(f"Received file: {name}")
    pfile_path = Path(f"/tmp/{pfile_uid[:2]}/{pfile_uid[2:4]}/{name}")
    pfile_path.parent.mkdir(parents=True, exist_ok=True)

    with open(pfile_path, "wb") as f:
        f.write(data)

    return dict(path=str(pfile_path))


@router.post(
    "/upload-to-storage",
    status_code=status.HTTP_201_CREATED,
)
async def upload_local_files_to_storage(
    payload: files_schemas.UploadFilesToRemoteStorageCreate,
):
    """For list of files in the request, upload the file from local storage to
    remote storage, using presigned URL. Files are uploaded in parallel for
    efficiency.
    """
    async with httpx.AsyncClient(verify=False) as client:
        await asyncio.gather(
            *[_upload_file_using_presigned_url(client, file) for file in payload.files]
        )


async def _upload_file_using_presigned_url(
    client: httpx.AsyncClient,
    file: files_schemas.UploadFileUsingPresignedUrl,
):
    logger.info(f"Uploading {file.local_file_path} to {file.upload_url}")
    with open(file.local_file_path, "rb") as f:
        response = await client.post(
            file.upload_url, files={"file": f}, data=file.upload_fields
        )
        if response.status_code >= 300:
            raise Exception(
                f"Error uploading file using presigned URL: {response.text}"
            )
