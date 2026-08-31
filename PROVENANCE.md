# Source provenance

Open Alarm is an Apache-2.0 project developed in this repository. This document records how source provenance is handled and where the limits of that record are.

## Project-authored source

Files in this repository are treated as Open Alarm project source unless they are explicitly identified as third-party material in the file itself or in `open_alarm/THIRD_PARTY_NOTICES.md`.

The current tree does not intentionally vendor third-party application source code, JavaScript libraries, fonts, images or other copied binary assets. Runtime and build dependencies are obtained from their normal package/container registries and are listed separately in the third-party notices.

A repository scan performed for the `0.1.0-beta.2` release found no third-party copyright headers or SPDX headers embedded in Open Alarm application source files. Absence of a header is not proof of originality, so this is only one part of the provenance review.

## AI-assisted development

Parts of Open Alarm were developed with generative-AI assistance.

A generative model does not provide Open Alarm with a searchable record that maps each generated line or snippet to a specific training-data source. The project therefore cannot truthfully claim that a model-generated function came from a particular upstream repository, nor can it produce per-line training-data attribution.

Because that provenance cannot be reconstructed from the model, Open Alarm applies the following release rule instead:

- generated code is reviewed as project code, not represented as copied third-party code;
- obvious third-party copyright/license headers are not removed or rewritten;
- distinctive copied code discovered during review must either be replaced with a clean project implementation or retained only when its source/license/required notices are documented;
- code with unknown or incompatible provenance is not knowingly accepted;
- dependencies are audited independently of who wrote the calling code.

This policy is not a legal guarantee that no generated sequence can resemble code that exists elsewhere. It is a transparent statement of the provenance information actually available to the project.

## Home Assistant references

Open Alarm implements public Home Assistant App, Ingress, Supervisor/Core API and frontend interfaces. Names such as `homeassistant_api`, `ingress`, `panel_icon`, Home Assistant entity IDs, REST/WebSocket command names and required App metadata keys are interface identifiers, not copied library source.

Home Assistant developer documentation is used as reference material for those interfaces. The developer-documentation repository is licensed separately from Open Alarm (currently Creative Commons Attribution-NonCommercial-ShareAlike 4.0). Open Alarm does not intentionally redistribute that documentation or substantial code examples from it. Documentation wording and implementation code in this repository should be independently written; mandatory API/configuration identifiers are used only as needed for interoperability.

Open Alarm uses the official Home Assistant base image because Home Assistant explicitly provides those images for building Apps. The base image and its included software retain their own upstream licenses; see `open_alarm/THIRD_PARTY_NOTICES.md`.

## Contributions

A contribution must be one of the following:

1. work the contributor has the right to submit under Apache-2.0;
2. a clean implementation based on public behavior/interfaces rather than copied protected source; or
3. third-party material whose license permits the intended use and whose source, license, modifications and required notices are disclosed before merge.

Do not paste code from Stack Overflow, blogs, closed-source products, commercial SDKs, other repositories or AI outputs represented as coming from a particular source unless the right to redistribute it has been established.

If provenance is uncertain, replace the code rather than guessing.

## Reporting a provenance concern

If you recognize source that may have been copied without the required license or attribution, report it using the process in `SECURITY.md` or open a non-sensitive issue identifying the file and upstream source. The preferred remediation is to establish the license/notice requirements or replace the affected implementation cleanly.
