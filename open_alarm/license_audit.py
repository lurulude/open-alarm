"""Audit installed Python dependency licenses without third-party audit tooling."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import deque
from importlib import metadata
from pathlib import Path

_ALLOWED_SPDX = {
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CC0-1.0",
    "ISC",
    "MIT",
    "MIT-0",
    "MPL-2.0",
    "PSF-2.0",
    "Python-2.0",
    "Unlicense",
    "Zlib",
}

_CLASSIFIER_LICENSES = {
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: BSD License": "BSD",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: The Unlicense (Unlicense)": "Unlicense",
    "License :: OSI Approved :: Zero-Clause BSD (0BSD)": "0BSD",
}

_RECOGNIZED_LICENSE_TEXT = {
    "apache license": "Apache-2.0",
    "bsd 2-clause": "BSD-2-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "bsd license": "BSD",
    "isc license": "ISC",
    "mit license": "MIT",
    "mozilla public license 2.0": "MPL-2.0",
    "permission is hereby granted, free of charge": "MIT",
    "python software foundation license": "PSF-2.0",
}

_OPERATOR_TOKENS = {"AND", "OR", "WITH"}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
_SPDX_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str | None:
    match = _NAME_RE.match(requirement.strip())
    return match.group(0) if match else None


def _validate_spdx(expression: str) -> tuple[bool, str]:
    tokens = [token for token in _SPDX_TOKEN_RE.findall(expression) if token not in _OPERATOR_TOKENS]
    if not tokens:
        return False, expression
    unknown = sorted({token for token in tokens if token not in _ALLOWED_SPDX})
    return not unknown, expression if not unknown else f"{expression} (unreviewed: {', '.join(unknown)})"


def _license_for(dist: metadata.Distribution) -> tuple[bool, str, str]:
    expression = (dist.metadata.get("License-Expression") or "").strip()
    if expression and expression.upper() not in {"UNKNOWN", "NONE"}:
        ok, detail = _validate_spdx(expression)
        return ok, expression, detail

    classifiers = dist.metadata.get_all("Classifier") or []
    matched = [_CLASSIFIER_LICENSES[item] for item in classifiers if item in _CLASSIFIER_LICENSES]
    if matched:
        return True, " OR ".join(sorted(set(matched))), "classifier"

    raw = (dist.metadata.get("License") or "").strip()
    lowered = raw.lower()
    for marker, normalized in _RECOGNIZED_LICENSE_TEXT.items():
        if marker in lowered:
            return True, normalized, "license metadata"

    return False, raw or "UNKNOWN", "missing or unreviewed license metadata"


def _installed_closure(roots: list[str]) -> list[metadata.Distribution]:
    queue = deque(roots)
    visited: set[str] = set()
    result: list[metadata.Distribution] = []

    while queue:
        requested = queue.popleft()
        key = _canonical(requested)
        if key in visited:
            continue
        try:
            dist = metadata.distribution(requested)
        except metadata.PackageNotFoundError as exc:
            raise SystemExit(f"Required audit root is not installed: {requested}") from exc

        actual_key = _canonical(dist.metadata.get("Name") or requested)
        if actual_key in visited:
            continue
        visited.add(actual_key)
        result.append(dist)

        for requirement in dist.requires or []:
            child = _requirement_name(requirement)
            if not child:
                continue
            try:
                metadata.distribution(child)
            except metadata.PackageNotFoundError:
                # Optional/marker-controlled requirement not installed in this environment.
                continue
            if _canonical(child) not in visited:
                queue.append(child)

    return sorted(result, key=lambda item: _canonical(item.metadata.get("Name") or ""))


def _copy_license_files(dist: metadata.Distribution, destination: Path) -> list[str]:
    copied: list[str] = []
    package_name = dist.metadata.get("Name") or "unknown"
    package_version = dist.version
    package_dir = destination / f"{package_name}-{package_version}"
    declared = {Path(value).name for value in (dist.metadata.get_all("License-File") or [])}

    for entry in dist.files or []:
        entry_path = Path(str(entry))
        basename = entry_path.name
        upper = basename.upper()
        if basename not in declared and not upper.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
            continue
        source = Path(dist.locate_file(entry))
        if not source.is_file():
            continue
        package_dir.mkdir(parents=True, exist_ok=True)
        target = package_dir / basename
        shutil.copyfile(source, target)
        copied.append(str(target.relative_to(destination)))

    return sorted(set(copied))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+", help="Installed top-level distributions to audit")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--copy-license-files", type=Path)
    args = parser.parse_args()

    distributions = _installed_closure(args.roots)
    report: list[dict[str, object]] = []
    failures: list[str] = []

    if args.copy_license_files:
        args.copy_license_files.mkdir(parents=True, exist_ok=True)

    for dist in distributions:
        name = dist.metadata.get("Name") or "unknown"
        ok, license_name, source = _license_for(dist)
        copied: list[str] = []
        if args.copy_license_files:
            copied = _copy_license_files(dist, args.copy_license_files)
        row = {
            "name": name,
            "version": dist.version,
            "license": license_name,
            "license_source": source,
            "license_files": copied,
        }
        report.append(row)
        print(f"{name}=={dist.version}: {license_name}")
        if not ok:
            failures.append(f"{name}=={dist.version}: {license_name} ({source})")
        if args.copy_license_files and not copied:
            failures.append(f"{name}=={dist.version}: no distributable LICENSE/NOTICE file found")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if failures:
        print("\nUnapproved or incomplete Python dependency license records:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"Audited {len(report)} installed Python distributions; all licenses are approved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
