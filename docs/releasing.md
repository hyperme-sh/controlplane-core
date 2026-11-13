# Releasing controlplane-core

This repository has no package or wheel distribution and no standalone container image. Service releases are the container images of the consuming services, tagged with channel or version tags only.

Pull-request version policy still checks the declared version line. Tag-time admission, when used, stays in `.github/workflows/release-admission.yml`. That workflow does not publish. The former package release-channel workflow has been removed.

Public container publication and the remaining Apache repositories are tracked in [FyberLabs/infra#993](https://github.com/FyberLabs/infra/issues/993).
