"""
SEEK — Azure Function for the Extract and Load steps.

Triggers
    run_pipeline_daily   Timer trigger, every day at 07:00 UTC (10:00 Riyadh).
                         The production path.
    run_pipeline_manual  HTTP trigger for quick manual tests. Azure limits HTTP
                         responses to about 230 seconds, so a full run can time
                         out here; use the timer for real runs.

Each run
    1. Downloads the current data/raw/ folder from the landing container in
       Azure Data Lake into a temporary copy of the app (the deployed folder is
       read-only at runtime).
    2. Runs main.py from that copy, which extracts the day's data from Etimad.
    3. Uploads data/raw/ back to the landing container, skipping files whose
       content is unchanged (MD5 check).

Azure Data Factory reads the landing container and performs all processing.

Settings (Application settings, never in code)
    ADLS_ACCOUNT_NAME   storage account name
    ADLS_SAS_TOKEN      SAS token for the storage account
"""

import hashlib
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.filedatalake import ContentSettings, DataLakeServiceClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# Landing container that mirrors the local data/ folder.
CONTAINER_NAME = "seek-data-landing"

# Folder inside data/ that is synced with the container.
DATA_SUBFOLDER = "raw"


def get_file_system_client():
    """Return a client for the landing container, using the account name and SAS token."""
    account_name = os.getenv("ADLS_ACCOUNT_NAME")
    sas_token = os.getenv("ADLS_SAS_TOKEN")
    service_client = DataLakeServiceClient(
        account_url=f"https://{account_name}.dfs.core.windows.net",
        credential=sas_token,
    )
    return service_client.get_file_system_client(CONTAINER_NAME)


def download_data_folder(file_system_client, local_data_dir: Path):
    """Copy everything under 'data/raw/' in the container into local_data_dir/raw."""
    remote_prefix = f"data/{DATA_SUBFOLDER}"
    try:
        paths = list(file_system_client.get_paths(path=remote_prefix))
    except ResourceNotFoundError:
        logging.info(f"No '{remote_prefix}/' folder in ADLS yet — first-ever run, starting empty.")
        return

    downloaded = 0
    for path in paths:
        if path.is_directory:
            continue
        relative = Path(path.name).relative_to("data")  # keeps "raw/<file>" as the local layout
        local_path = local_data_dir / relative
        local_path.parent.mkdir(parents=True, exist_ok=True)

        file_client = file_system_client.get_file_client(path.name)
        download = file_client.download_file()
        with open(local_path, "wb") as f:
            f.write(download.readall())
        downloaded += 1

    logging.info(f"Downloaded {downloaded} files from ADLS ({remote_prefix}/) into {local_data_dir}")


def compute_md5(file_path: Path) -> bytes:
    """Return the raw MD5 digest of a local file's content."""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.digest()


def upload_data_folder(file_system_client, local_data_dir: Path):
    """Upload everything under local_data_dir/raw to 'data/raw/' in the container.

    A file is skipped when its MD5 matches the content_md5 stored on the
    remote file.
    """
    local_raw_dir = local_data_dir / DATA_SUBFOLDER
    if not local_raw_dir.exists():
        logging.info(f"No local {DATA_SUBFOLDER}/ folder to upload — nothing to do.")
        return

    uploaded = 0
    skipped = 0

    for local_file in local_raw_dir.rglob("*"):
        if not local_file.is_file():
            continue
        remote_path = "data/" + str(local_file.relative_to(local_data_dir)).replace(os.sep, "/")

        directory = str(Path(remote_path).parent)
        directory_client = file_system_client.get_directory_client(directory)
        try:
            directory_client.create_directory()
        except Exception:
            pass  # directory already exists

        file_client = file_system_client.get_file_client(remote_path)
        local_hash = compute_md5(local_file)

        try:
            remote_props = file_client.get_file_properties()
            remote_hash = remote_props.content_settings.content_md5
        except Exception:
            remote_hash = None  # file does not exist remotely yet

        if remote_hash == local_hash:
            skipped += 1
            continue

        with open(local_file, "rb") as f:
            data = f.read()
        file_client.upload_data(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_md5=local_hash),
        )
        uploaded += 1

    logging.info(
        f"Uploaded {uploaded} changed file(s), skipped {skipped} unchanged file(s), "
        f"from {local_raw_dir} back to ADLS (data/{DATA_SUBFOLDER}/)"
    )


def execute_pipeline():
    """Download raw data, run the extraction, upload the result. Raises on failure."""
    deployed_dir = Path(__file__).resolve().parent
    run_dir = Path(tempfile.mkdtemp(prefix="seek_run_"))

    try:
        shutil.copytree(deployed_dir / "src", run_dir / "src")
        shutil.copy(deployed_dir / "main.py", run_dir / "main.py")

        local_data_dir = run_dir / "data"
        local_data_dir.mkdir(parents=True, exist_ok=True)

        file_system_client = get_file_system_client()

        logging.info("Step 1/3: downloading current data/raw/ state from ADLS...")
        download_data_folder(file_system_client, local_data_dir)

        logging.info("Step 2/3: running the pipeline (main.py, Task 1 only)...")
        sys.path.insert(0, str(run_dir))
        import main as seek_main
        seek_main.main()

        logging.info("Step 3/3: uploading updated data/raw/ state back to ADLS...")
        upload_data_folder(file_system_client, local_data_dir)

        logging.info("Pipeline run complete.")

    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


@app.timer_trigger(schedule="0 0 7 * * *", arg_name="myTimer", run_on_startup=False, use_monitor=True)
def run_pipeline_daily(myTimer: func.TimerRequest) -> None:
    """Scheduled daily run at 07:00 UTC (10:00 Riyadh)."""
    logging.info("SEEK pipeline triggered by daily schedule.")
    try:
        execute_pipeline()
    except Exception:
        logging.exception("Scheduled pipeline run failed")
        raise


@app.route(route="run_pipeline", methods=["POST", "GET"])
def run_pipeline_manual(req: func.HttpRequest) -> func.HttpResponse:
    """Manual run over HTTP, for quick tests only (subject to the ~230 s HTTP limit)."""
    logging.info("SEEK pipeline function triggered manually via HTTP.")
    try:
        execute_pipeline()
        return func.HttpResponse("Pipeline completed successfully.", status_code=200)
    except Exception as e:
        logging.exception("Pipeline failed")
        return func.HttpResponse(f"Pipeline failed: {e}", status_code=500)
