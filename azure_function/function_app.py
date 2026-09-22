"""
SEEK Pipeline — Azure Function entry point
============================================
Two triggers, sharing the same core logic:

  - run_pipeline_daily (Timer trigger): the REAL scheduled path. Runs every
    day at 06:00. Timer triggers have no hard execution-time limit, so this
    is safe for however long the pipeline actually takes.

  - run_pipeline_manual (HTTP trigger): for quick manual testing only.
    Azure enforces a hard 230-second reply limit on ALL HTTP-triggered
    functions (an Azure Load Balancer default, not something host.json's
    functionTimeout can override) — so this endpoint will time out on a
    full run. Fine for a quick smoke test; it is not the production path.

Design choice for the pipeline itself: rather than rewriting extract.py to
read and write ADLS directly, each run:

  1. Downloads the current data/raw/ folder from ADLS into a fresh /tmp
     working copy of the app (main.py + src/), since the deployed app
     folder itself is read-only at runtime.
  2. Runs main.py from that copy, completely unmodified.
  3. Uploads the updated data/raw/ folder back to ADLS.

NOTE (post-ADF-pivot): main.py now runs ONLY Task 1 (extraction). Tasks
2-4 (clean/translate/classify, validate, archive) have moved to Aseel's
Azure Data Factory pipeline, which reads from this Function's output
container (seek-data-landing) separately. This Function no longer touches
data/interim/ or data/processed/ — those folders no longer exist in this
Function's output, they only ever existed for the now-removed Tasks 2-4.
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

# Name of the ADLS container that mirrors the local data/ folder.
CONTAINER_NAME = "seek-data-landing"

# Only Task 1's output folder needs to round-trip through this Function now.
DATA_SUBFOLDER = "raw"


def get_file_system_client():
    account_name = os.getenv("ADLS_ACCOUNT_NAME")
    sas_token = os.getenv("ADLS_SAS_TOKEN")
    service_client = DataLakeServiceClient(
        account_url=f"https://{account_name}.dfs.core.windows.net",
        credential=sas_token,
    )
    return service_client.get_file_system_client(CONTAINER_NAME)


def download_data_folder(file_system_client, local_data_dir: Path):
    """Mirrors everything under 'data/raw/' in the container into local_data_dir/raw."""
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
    """Returns the raw MD5 digest of a local file's content."""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.digest()


def upload_data_folder(file_system_client, local_data_dir: Path):
    """Uploads everything under local_data_dir/raw back to 'data/raw/' in the
    container — but skips any file whose content hasn't actually changed
    since the last upload, so "Last modified" reflects real changes only."""
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
            pass  # already exists — fine

        file_client = file_system_client.get_file_client(remote_path)
        local_hash = compute_md5(local_file)

        try:
            remote_props = file_client.get_file_properties()
            remote_hash = remote_props.content_settings.content_md5
        except Exception:
            remote_hash = None  # file doesn't exist remotely yet — must upload

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
    """Core logic shared by both triggers. Raises on failure."""
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
    logging.info("SEEK pipeline triggered by daily schedule.")
    try:
        execute_pipeline()
    except Exception:
        logging.exception("Scheduled pipeline run failed")
        raise


@app.route(route="run_pipeline", methods=["POST", "GET"])
def run_pipeline_manual(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("SEEK pipeline function triggered manually via HTTP.")
    try:
        execute_pipeline()
        return func.HttpResponse("Pipeline completed successfully.", status_code=200)
    except Exception as e:
        logging.exception("Pipeline failed")
        return func.HttpResponse(f"Pipeline failed: {e}", status_code=500)