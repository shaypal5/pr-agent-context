from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from pr_agent_context.github.api import GitHubApiClient, GitHubApiError


def discover_coverage_files(root: Path | None) -> list[Path]:
    if root is None or not root.exists():
        return []
    coverage_files = [
        path
        for path in root.rglob(".coverage*")
        if path.is_file() and not _is_transient_coverage_file(path)
    ]
    return sorted(coverage_files)


def discover_coverage_report_files(root: Path | None, *, report_filename: str) -> list[Path]:
    if root is None or not root.exists():
        return []
    normalized_target = PurePosixPath(report_filename).as_posix().lstrip("./")
    target_name = PurePosixPath(normalized_target).name
    report_files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative_path = PurePosixPath(path.relative_to(root).as_posix()).as_posix()
        if relative_path == normalized_target or path.name == target_name:
            report_files.append(path)
    return sorted(set(report_files))


def resolve_coverage_files(
    *,
    client: GitHubApiClient,
    owner: str,
    repo: str,
    head_sha: str,
    local_artifacts_dir: Path | None,
    patch_coverage_source_mode: str = "raw_coverage_artifacts",
    artifact_prefix: str,
    coverage_report_artifact_name: str = "",
    coverage_report_filename: str = "coverage.xml",
    enable_cross_run_lookup: bool,
    execution_mode: str,
    workflow_names: tuple[str, ...],
    allowed_conclusions: tuple[str, ...],
    selection_strategy: str,
    max_candidate_runs: int,
) -> tuple[list[Path], dict[str, object]]:
    local_files = _discover_local_inputs(
        local_artifacts_dir,
        patch_coverage_source_mode=patch_coverage_source_mode,
        coverage_report_filename=coverage_report_filename,
    )
    debug: dict[str, object] = {
        "execution_mode": execution_mode,
        "patch_coverage_source_mode": patch_coverage_source_mode,
        "local_artifact_dir": str(local_artifacts_dir) if local_artifacts_dir else None,
        "local_coverage_files": [str(path) for path in local_files],
        "cross_run_lookup_enabled": enable_cross_run_lookup,
        "artifact_prefix": artifact_prefix,
        "coverage_report_artifact_name": coverage_report_artifact_name,
        "coverage_report_filename": coverage_report_filename,
        "workflow_name_filter": list(workflow_names),
        "allowed_conclusions": list(allowed_conclusions),
        "selection_strategy": selection_strategy,
        "candidate_runs": [],
        "selected_run": None,
        "selected_artifacts": [],
        "resolution": "",
        "coverage_source_pending": False,
        "warnings": [],
    }

    if execution_mode == "ci" and local_files:
        debug["resolution"] = "local_current_run_artifacts"
        return local_files, debug

    if not enable_cross_run_lookup:
        debug["resolution"] = "cross_run_lookup_disabled"
        return local_files, debug

    selected_run = _select_coverage_source_run(
        client,
        owner=owner,
        repo=repo,
        head_sha=head_sha,
        patch_coverage_source_mode=patch_coverage_source_mode,
        artifact_prefix=artifact_prefix,
        coverage_report_artifact_name=coverage_report_artifact_name,
        workflow_names=workflow_names,
        allowed_conclusions=allowed_conclusions,
        selection_strategy=selection_strategy,
        max_candidate_runs=max_candidate_runs,
        debug=debug,
    )
    if selected_run is None:
        if local_files:
            debug["resolution"] = "local_current_run_artifacts_fallback"
        elif debug["coverage_source_pending"]:
            debug["resolution"] = "coverage_source_pending"
        else:
            debug["resolution"] = "no_suitable_coverage_source"
        return local_files, debug

    destination_root = local_artifacts_dir or Path(
        tempfile.mkdtemp(prefix="pr-agent-context-coverage-source-")
    )
    destination_root.mkdir(parents=True, exist_ok=True)
    extracted_root = destination_root / f"source-run-{selected_run['id']}"
    extracted_root.mkdir(parents=True, exist_ok=True)

    selected_artifacts = selected_run["matching_artifacts"]
    extracted_dirs: list[str] = []
    for artifact in selected_artifacts:
        artifact_dir = extracted_root / str(artifact["id"])
        artifact_dir.mkdir(parents=True, exist_ok=True)
        _download_artifact_zip(
            client,
            owner=owner,
            repo=repo,
            artifact_id=int(artifact["id"]),
            destination=artifact_dir,
        )
        extracted_dirs.append(str(artifact_dir))

    debug["selected_run"] = {
        "id": selected_run["id"],
        "name": selected_run["name"],
        "status": selected_run["status"],
        "conclusion": selected_run["conclusion"],
        "updated_at": selected_run["updated_at"],
    }
    debug["selected_artifacts"] = [
        {
            "id": artifact["id"],
            "name": artifact["name"],
            "size_in_bytes": artifact["size_in_bytes"],
        }
        for artifact in selected_artifacts
    ]
    debug["downloaded_dirs"] = extracted_dirs
    files = _discover_local_inputs(
        extracted_root,
        patch_coverage_source_mode=patch_coverage_source_mode,
        coverage_report_filename=coverage_report_filename,
    )
    debug["resolution"] = "cross_run_downloaded" if files else "selected_run_without_coverage_files"
    debug["selected_coverage_files"] = [str(path) for path in files]
    return files, debug


def _select_coverage_source_run(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    patch_coverage_source_mode: str = "raw_coverage_artifacts",
    artifact_prefix: str,
    coverage_report_artifact_name: str = "",
    workflow_names: tuple[str, ...],
    allowed_conclusions: tuple[str, ...],
    selection_strategy: str,
    max_candidate_runs: int,
    debug: dict[str, object],
) -> dict[str, object] | None:
    if selection_strategy != "latest_successful":
        raise ValueError(f"Unsupported coverage selection strategy: {selection_strategy}")

    warnings = debug["warnings"]
    payload = _safe_request_json(
        client,
        "GET",
        f"/repos/{owner}/{repo}/actions/runs",
        params={"head_sha": head_sha, "per_page": 100},
        warnings=warnings,
        warning_prefix=f"Unable to fetch candidate coverage runs for head SHA {head_sha}",
    )
    if not payload:
        return None

    raw_runs = list(payload.get("workflow_runs", []))
    sorted_runs = sorted(
        raw_runs,
        key=lambda run: (
            str(run.get("updated_at") or run.get("created_at") or ""),
            int(run.get("id") or 0),
        ),
        reverse=True,
    )[:max_candidate_runs]

    selected: dict[str, object] | None = None
    pending_candidate_found = False
    for run in sorted_runs:
        run_name = str(run.get("name") or "")
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        record = {
            "id": int(run.get("id") or 0),
            "name": run_name,
            "status": status,
            "conclusion": conclusion,
            "updated_at": str(run.get("updated_at") or ""),
            "accepted": False,
            "reasons": [],
            "matching_artifacts": [],
        }
        if workflow_names and run_name not in workflow_names:
            record["reasons"].append("workflow_name_filtered")
            debug["candidate_runs"].append(record)
            continue
        if status and status != "completed":
            record["reasons"].append("not_completed_yet")
            debug["candidate_runs"].append(record)
            pending_candidate_found = True
            continue
        if conclusion not in allowed_conclusions:
            record["reasons"].append("conclusion_filtered")
            debug["candidate_runs"].append(record)
            continue

        artifacts = _list_run_artifacts(
            client,
            owner=owner,
            repo=repo,
            run_id=int(run["id"]),
            warnings=warnings,
        )
        matching_artifacts = _match_coverage_source_artifacts(
            artifacts,
            patch_coverage_source_mode=patch_coverage_source_mode,
            artifact_prefix=artifact_prefix,
            coverage_report_artifact_name=coverage_report_artifact_name,
        )
        if not matching_artifacts:
            record["reasons"].append("no_matching_artifacts")
            debug["candidate_runs"].append(record)
            continue

        record["accepted"] = True
        record["matching_artifacts"] = [
            {
                "id": int(artifact["id"]),
                "name": str(artifact.get("name") or ""),
                "size_in_bytes": int(artifact.get("size_in_bytes") or 0),
            }
            for artifact in matching_artifacts
        ]
        debug["candidate_runs"].append(record)
        if selected is None:
            selected = {
                "id": int(run["id"]),
                "name": run_name,
                "status": status,
                "conclusion": conclusion,
                "updated_at": str(run.get("updated_at") or ""),
                "matching_artifacts": record["matching_artifacts"],
            }
    debug["coverage_source_pending"] = pending_candidate_found
    return selected


def _list_run_artifacts(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    run_id: int,
    warnings: list[str],
) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    page = 1
    while True:
        payload = _safe_request_json(
            client,
            "GET",
            f"/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts",
            params={"per_page": 100, "page": page},
            warnings=warnings,
            warning_prefix=f"Unable to fetch artifacts for run {run_id}",
        )
        if not payload:
            break
        page_artifacts = list(payload.get("artifacts", []))
        if not page_artifacts:
            break
        artifacts.extend(page_artifacts)
        if len(page_artifacts) < 100:
            break
        page += 1
    return artifacts


def _download_artifact_zip(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    artifact_id: int,
    destination: Path,
) -> None:
    zip_bytes = client.request_bytes_following_redirect_without_auth(
        "GET",
        f"/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip",
    )
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        archive.extractall(destination)


def _safe_request_json(
    client: GitHubApiClient,
    method: str,
    path: str,
    *,
    params: dict[str, object] | None,
    warnings: list[str],
    warning_prefix: str,
) -> dict[str, object] | None:
    try:
        return client.request_json(method, path, params=params)
    except GitHubApiError as error:
        warnings.append(f"{warning_prefix}: {error}")
        return None


def _is_transient_coverage_file(path: Path) -> bool:
    return path.name.endswith(("-shm", "-wal", ".lock"))


def _discover_local_inputs(
    root: Path | None,
    *,
    patch_coverage_source_mode: str,
    coverage_report_filename: str,
) -> list[Path]:
    if patch_coverage_source_mode == "coverage_xml_artifact":
        return discover_coverage_report_files(root, report_filename=coverage_report_filename)
    return discover_coverage_files(root)


def _match_coverage_source_artifacts(
    artifacts: list[dict[str, object]],
    *,
    patch_coverage_source_mode: str,
    artifact_prefix: str,
    coverage_report_artifact_name: str,
) -> list[dict[str, object]]:
    if patch_coverage_source_mode == "coverage_xml_artifact":
        return [
            artifact
            for artifact in artifacts
            if str(artifact.get("name") or "") == coverage_report_artifact_name
        ]
    return [
        artifact
        for artifact in artifacts
        if str(artifact.get("name") or "").startswith(artifact_prefix)
    ]
