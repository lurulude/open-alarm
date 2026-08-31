# Third-party software and notices

Open Alarm itself is licensed under Apache-2.0. Third-party packages, build tools and container-base components retain their own licenses and copyrights. This file records the release audit; it does not relicense third-party software under the Open Alarm license.

Audit date: **2026-08-31** for **0.1.0-beta.2**.

The audited Open Alarm Python/npm dependency graphs contain **no proprietary, commercial-only, noncommercial-only or field-of-use-restricted package**. The project license gate also rejects unknown licenses instead of treating them as acceptable by default.

The release build writes the actual installed dependency inventories to `/app/licenses/python-packages.json` and `/app/licenses/npm-packages.json`. Python package license files found in installed package metadata are copied under `/app/licenses/python/`. Exact React/React DOM/Scheduler license files are copied under `/app/licenses/frontend/` because those libraries are incorporated into the production browser bundle.

Dependency ranges may select newer compatible versions in a future build. Every CI/App build therefore audits the packages actually installed; an unreviewed license fails the build.

## 1. Runtime Python dependency graph

Direct runtime requirements are declared in `requirements.txt`. The following exact runtime graph was resolved and reviewed for the Beta.2 release build:

| Package | Version | License |
| --- | ---: | --- |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.14.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| FastAPI | 0.141.1 | MIT |
| h11 | 0.16.0 | MIT |
| idna | 3.19 | BSD-3-Clause |
| Pydantic | 2.13.5 | MIT |
| pydantic-core | 2.46.5 | MIT |
| Starlette | 1.6.0 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| Uvicorn | 0.52.4 | BSD-3-Clause |
| websockets | 17.1 | BSD-3-Clause |

Direct upstream projects:

- FastAPI: `https://github.com/fastapi/fastapi`
- Uvicorn: `https://github.com/Kludex/uvicorn`
- Pydantic: `https://github.com/pydantic/pydantic`
- websockets: `https://github.com/python-websockets/websockets`

The final App image keeps each installed Python distribution's own `.dist-info` metadata. The release build additionally copies discovered `LICENSE`, `LICENCE`, `COPYING` and `NOTICE` files for the audited runtime closure into `/app/licenses/python/`.

## 2. Frontend dependency/build graph

The frontend package manifest has two production dependencies (`react` and `react-dom`) plus build/type-check tooling. `scheduler` is pulled by React DOM. The Beta.2 build resolved the following complete npm graph on the Linux CI/build platform:

| Package | Version | License |
| --- | ---: | --- |
| @oxc-project/types | 0.147.0 | MIT |
| @rolldown/binding-linux-x64-gnu | 1.2.6 | MIT |
| @rolldown/pluginutils | 1.0.1 | MIT |
| @types/react | 19.2.18 | MIT |
| @types/react-dom | 19.2.5 | MIT |
| @typescript/typescript-linux-x64 | 7.0.2 | Apache-2.0 |
| @vitejs/plugin-react | 6.1.1 | MIT |
| csstype | 3.2.3 | MIT |
| detect-libc | 2.1.2 | Apache-2.0 |
| fdir | 6.5.0 | MIT |
| lightningcss | 1.33.0 | MPL-2.0 |
| lightningcss-linux-x64-gnu | 1.33.0 | MPL-2.0 |
| nanoid | 3.3.18 | MIT |
| picocolors | 1.1.1 | ISC |
| picomatch | 4.0.7 | MIT |
| postcss | 8.5.26 | MIT |
| react | 19.2.8 | MIT |
| react-dom | 19.2.8 | MIT |
| rolldown | 1.2.6 | MIT |
| scheduler | 0.27.0 | MIT |
| source-map-js | 1.2.1 | BSD-3-Clause |
| tinyglobby | 0.2.17 | MIT |
| typescript | 7.0.2 | Apache-2.0 |
| vite | 8.2.2 | MIT |

Only the compiled production bundle is copied from the Node builder stage into the final App image; `node_modules` and build tooling are not copied. React, React DOM and Scheduler code are incorporated into that bundle, so their exact upstream license files are retained in `/app/licenses/frontend/`.

React/React DOM/Scheduler are distributed under the MIT License with this upstream copyright notice:

> Copyright (c) Meta Platforms, Inc. and affiliates.

MIT permission notice:

> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

`lightningcss` and its platform binding are MPL-2.0. They are build-time packages only; Open Alarm does not modify or copy their source/package into the final App image. Their license remains available in the npm package used during the local build.

Open Alarm does not bundle font files or third-party image assets. CSS names common system fonts (`Inter`, `Roboto`, `Arial`) as font-family preferences only. `mdi:*` values are Home Assistant icon identifiers; Open Alarm does not redistribute Material Design Icons artwork.

## 3. Test-only Python/CI graph

The normal CI test command adds `httpx2`, `pytest` and `ruff`. The additional packages unique to or needed by that test graph in the Beta.2 audit were:

| Package | Version | License |
| --- | ---: | --- |
| httpcore2 | 2.12.0 | BSD-3-Clause |
| httpx2 | 2.12.0 | BSD-3-Clause |
| iniconfig | 2.3.0 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.6.0 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pytest | 9.1.1 | MIT |
| Ruff | 0.16.5 | MIT |
| truststore | 0.10.4 | MIT |

Shared runtime packages such as `anyio`, `h11` and `idna` are already listed above. These test packages are installed on CI runners and are not added to the final Open Alarm App image.

GitHub Actions used by the workflow are third-party execution tooling and are fetched by GitHub Actions rather than shipped in Open Alarm:

| Action | License |
| --- | --- |
| actions/checkout | MIT |
| actions/setup-python | MIT |
| actions/setup-node | MIT |

## 4. Container bases

Open Alarm's Dockerfile references:

- `node:24-alpine` for the disposable frontend builder stage;
- `ghcr.io/home-assistant/base-python:3.13-alpine3.21` for the final Home Assistant App base.

The official Node Docker image packaging repository is MIT licensed. Node.js and Alpine components inside that image retain their own upstream licenses.

The Home Assistant `docker-base` repository is Apache-2.0 licensed and is published specifically as the base for Home Assistant container/App builds. Its resulting images contain multiple independent open-source operating-system/runtime components under their own licenses. The base image may therefore include permissive and reciprocal free-software licenses that are separate from Open Alarm's Apache-2.0 source license.

Open Alarm Beta.2 is distributed as a **source-built Home Assistant App repository**. Open Alarm does not publish a prebuilt combined Open Alarm image to a registry; the user's Home Assistant installation performs the image build locally from this Dockerfile and the referenced upstream bases. If the project later publishes prebuilt multi-architecture images, base-image redistribution/source-offer obligations must be audited again before publishing those images.

`run.sh` uses plain `/bin/sh`; Open Alarm no longer depends on Home Assistant Bashio APIs.

## 5. Home Assistant interfaces and reference documentation

Open Alarm interoperates with Home Assistant but does not vendor Home Assistant Core/frontend source code.

Home Assistant API names, entity IDs, App manifest keys, Supervisor/Core endpoint names and WebSocket command names are used as interoperability/interface identifiers. Home Assistant developer documentation is reference material and is licensed separately (currently CC BY-NC-SA 4.0). Open Alarm does not intentionally redistribute that documentation or substantial copied examples from it. See `../PROVENANCE.md`.

## 6. Source/assets provenance scan

The Beta.2 repository review found no intentionally vendored JavaScript/Python library source, third-party font file, image asset, minified vendor file, Stack Overflow attribution, copied GitHub gist, embedded third-party copyright header or SPDX header in Open Alarm application source.

That scan cannot prove that independently written or AI-assisted code has no textual similarity to code elsewhere. The limits of AI source provenance and the rule for uncertain code are documented in `../PROVENANCE.md`.

## 7. Enforced license policy

`license_audit.py` recursively audits the installed Python closure. `frontend/license-audit.mjs` audits every installed npm package. CI and the Docker build fail on missing/unreviewed license metadata.

The current allowlist is intentionally conservative: 0BSD, Apache-2.0, BSD-2-Clause, BSD-3-Clause, CC0-1.0, ISC, MIT, MIT-0, MPL-2.0, PSF-2.0, Python-2.0, Unlicense and Zlib. A new identifier is **not** accepted automatically; it must be reviewed before the allowlist changes.

Project policy rejects proprietary, source-available-but-restricted, noncommercial-only, field-of-use-restricted and unknown-license Python/npm dependencies. Strong-copyleft Python/npm dependencies are also not currently allowlisted, even though strong copyleft is free/open-source, because Open Alarm is intentionally minimizing redistribution obligations in its own dependency graph.

Permissive notices/copyrights must remain intact. Reciprocal licenses must be handled according to their own terms rather than represented as Apache-2.0. If a dependency changes to an unacceptable license, Open Alarm must pin an acceptable release, replace the dependency, or remove the affected feature.

## 8. No legal guarantee

This inventory is maintained to improve license compliance and provenance transparency. It is not legal advice and cannot guarantee the absence of all intellectual-property claims. Upstream package authors/registries remain authoritative for their license terms. A separate professional legal review is appropriate before commercial redistribution of prebuilt images or where contractual assurance is required.
