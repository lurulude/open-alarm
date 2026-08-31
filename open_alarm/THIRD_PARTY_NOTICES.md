# Third-party software and notices

Open Alarm itself is licensed under Apache-2.0. Third-party packages, container-base components and development tools retain their own licenses and copyrights. This file is an attribution/compliance inventory; it does not relicense third-party software under the Open Alarm license.

Audit date: **2026-08-31** (`0.1.0-beta.2`).

The current audit found **no proprietary/commercial-only dependency intentionally required by Open Alarm**. All direct application dependencies are published under OSI-approved or similarly permissive/free licenses. Some build/base-image components use reciprocal free-software licenses such as MPL-2.0 or GPL-family licenses; those components are not relicensed as Open Alarm source.

The exact transitive versions selected by package managers can change while dependency ranges remain open. CI therefore performs a license-policy check on the packages actually installed, and the App build writes package/license inventories into the built image.

## Runtime Python dependencies

The direct runtime dependencies declared in `requirements.txt` are:

| Package | Role | Upstream | License |
| --- | --- | --- | --- |
| FastAPI | HTTP/API framework | https://github.com/fastapi/fastapi | MIT |
| Uvicorn | ASGI server | https://github.com/encode/uvicorn | BSD-3-Clause |
| Pydantic | typed validation/models | https://github.com/pydantic/pydantic | MIT |
| websockets | Home Assistant WebSocket transport | https://github.com/python-websockets/websockets | BSD-3-Clause |

Current dependency families pulled by those packages include the following free/open-source packages. The exact installed versions are recorded at build time rather than frozen in this document:

| Package | Typical parent | License |
| --- | --- | --- |
| Starlette | FastAPI | BSD-3-Clause |
| AnyIO | Starlette | MIT |
| idna | AnyIO | BSD-3-Clause |
| annotated-doc | FastAPI | MIT |
| typing-extensions | FastAPI/Pydantic | PSF-2.0 |
| annotated-types | Pydantic | MIT |
| pydantic-core | Pydantic | MIT |
| typing-inspection | Pydantic | MIT |
| Click | Uvicorn | BSD-3-Clause |
| h11 | Uvicorn | MIT |

Python packages installed into the final App image normally include their package metadata and license files in their `.dist-info` directories. The App build additionally records the selected package names, versions and declared licenses under `/app/licenses/`.

## Bundled browser code

The production browser bundle contains Open Alarm code plus React runtime code:

| Package | Version declared by Open Alarm | Upstream | License |
| --- | --- | --- | --- |
| React | 19.2.8 | https://github.com/facebook/react | MIT |
| React DOM | 19.2.8 | https://github.com/facebook/react | MIT |
| Scheduler | dependency of React DOM | https://github.com/facebook/react | MIT |

React, React DOM and Scheduler are covered by the React MIT license:

> Copyright (c) Meta Platforms, Inc. and affiliates.
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Open Alarm does not bundle font files or third-party image assets. CSS names common system fonts (`Inter`, `Roboto`, `Arial`) only as browser font-family preferences. The optional `mdi:*` values are Home Assistant icon identifiers; Open Alarm does not redistribute Material Design Icons artwork.

## Frontend build-only dependencies

These packages are used to type-check/build the frontend. They are installed in the Docker **builder stage** and are not copied as `node_modules` into the final App image.

| Package | Version declared | License |
| --- | --- | --- |
| Vite | 8.2.2 | MIT |
| TypeScript | 7.0.2 | Apache-2.0 |
| @types/react | 19.2.18 | MIT |
| @types/react-dom | 19.2.5 | MIT |

The Vite dependency family currently includes free/open-source packages such as Rolldown (MIT), Lightning CSS (MPL-2.0), PostCSS (MIT), picomatch (MIT), tinyglobby (MIT), fdir (MIT), `@oxc-project/types` (MIT), `@rolldown/pluginutils` (MIT), picocolors (ISC) and source-map-js (BSD-3-Clause). Vite's own distributed package also carries notices for bundled code under permissive/free licenses including Apache-2.0, BSD-2-Clause, CC0-1.0, ISC and MIT.

`lightningcss` is MPL-2.0 licensed. Open Alarm does not modify or vendor Lightning CSS source and does not copy its package into the final App image; it is build tooling selected by Vite.

## Test and CI tooling

Development/CI uses software that is not shipped as part of the Open Alarm App runtime:

| Tool | Purpose | License |
| --- | --- | --- |
| pytest | Python tests | MIT |
| Ruff | Python lint | MIT |
| HTTPX2 | test HTTP client | BSD-3-Clause |
| httpcore2 | HTTPX2 dependency | BSD-3-Clause |
| truststore | HTTPX2 dependency where applicable | MIT |
| actions/checkout | GitHub Actions checkout | MIT |
| actions/setup-python | GitHub Actions Python setup | MIT |
| actions/setup-node | GitHub Actions Node setup | MIT |

Transitive CI-only packages are subject to the same automated license allowlist. They are not copied into the Open Alarm App image unless they are also runtime dependencies listed above.

## Container bases and operating-system software

Open Alarm currently builds from these container bases:

- `node:24-alpine` for the disposable frontend builder stage;
- `ghcr.io/home-assistant/base-python:3.13-alpine3.21` for the final Home Assistant App image.

The Node Docker image packaging project is MIT licensed. Node.js itself and software included in the image carry their own open-source licenses.

The Home Assistant base-image project is specifically published for Home Assistant App/add-on builds and includes Alpine Linux, Python, s6-overlay, Bashio, TempIO and other base components. Home Assistant projects and individual included components retain their own licenses. Examples include Python under the PSF license, Bashio under MIT, and s6-overlay under ISC. Alpine packages contain license identifiers in package metadata; the Open Alarm Docker build records the installed package/license metadata for the final image under `/app/licenses/`.

Open Alarm currently publishes **source-built** App releases rather than a prebuilt Open Alarm registry image. A Home Assistant installation therefore performs the container build locally. If Open Alarm later publishes prebuilt multi-architecture images, the release process must re-audit redistribution/source-offer obligations for every base-image component before those images are published.

## Home Assistant interfaces and documentation

Open Alarm interoperates with Home Assistant but does not vendor Home Assistant Core or frontend source code.

Home Assistant API names, entity IDs, App manifest keys, Supervisor/Core endpoint names and WebSocket command names are used as interoperability/interface identifiers. The Home Assistant developer documentation is reference material and is licensed separately (currently CC BY-NC-SA 4.0). Open Alarm does not intentionally incorporate substantial developer-documentation prose or code examples into its distributed source. See `../PROVENANCE.md` for the project provenance policy.

## License policy

New dependencies should be avoided unless they solve a concrete requirement. A dependency must not be added when its license is missing, proprietary, source-available-but-not-open-source, noncommercial-only, field-of-use restricted, or otherwise incompatible with the intended public distribution of Open Alarm.

The automated dependency audit accepts only explicitly reviewed free/open-source license identifiers. A newly encountered license causes CI to fail until it is reviewed; it is not silently added to the allowlist.

Permissive license notices/copyrights must remain intact. Reciprocal licenses must be handled according to their own terms rather than being represented as Apache-2.0. If a dependency's license changes incompatibly, Open Alarm should pin the last acceptable release, replace the dependency, or remove the affected feature.

## No warranty / legal review

This inventory is maintained to improve license compliance and provenance transparency. It is not legal advice and cannot eliminate all legal risk. Package authors and registries remain the authoritative source for their license terms. For commercial distribution or a prebuilt-image program, an independent legal/compliance review is appropriate.
