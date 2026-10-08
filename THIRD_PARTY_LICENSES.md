# Third-Party Licenses

This document contains the licenses and notices for third-party software included in or used by NVIDIA Config Manager.

## Table of Contents

- [Python Dependencies](#python-dependencies)
- [JavaScript/TypeScript Dependencies](#javascripttypescript-dependencies)
- [Go Dependencies](#go-dependencies)
- [Nautobot Component Dependencies](#nautobot-component-dependencies)
- [Runtime Container Images](#runtime-container-images)
- [Bundled CLI Tools](#bundled-cli-tools)
- [License Texts](#license-texts)

---

## Python Dependencies

The following Python packages are dependencies of NVIDIA Config Manager. See `pyproject.toml` for specific versions.

| Package | License | URL |
|---------|---------|-----|
| click | BSD-3-Clause | https://github.com/pallets/click |
| requests | Apache-2.0 | https://github.com/psf/requests |
| pydantic | MIT | https://github.com/pydantic/pydantic |
| pydantic-settings | MIT | https://github.com/pydantic/pydantic-settings |
| fastapi | MIT | https://github.com/tiangolo/fastapi |
| uvicorn | BSD-3-Clause | https://github.com/encode/uvicorn |
| starlette | BSD-3-Clause | https://github.com/encode/starlette |
| python-multipart | Apache-2.0 | https://github.com/andrew-d/python-multipart |
| prometheus-fastapi-instrumentator | ISC | https://github.com/trallnag/prometheus-fastapi-instrumentator |
| prometheus-client | Apache-2.0 | https://github.com/prometheus/client_python |
| aiohttp | Apache-2.0 | https://github.com/aio-libs/aiohttp |
| aiohttp-retry | MIT | https://github.com/inyutin/aiohttp_retry |
| httpx | BSD-3-Clause | https://github.com/encode/httpx |
| urllib3 | MIT | https://github.com/urllib3/urllib3 |
| boto3 | Apache-2.0 | https://github.com/boto/boto3 |
| aioboto3 | Apache-2.0 | https://github.com/terrycain/aioboto3 |
| sqlalchemy | MIT | https://github.com/sqlalchemy/sqlalchemy |
| asyncpg | Apache-2.0 | https://github.com/MagicStack/asyncpg |
| alembic | MIT | https://github.com/sqlalchemy/alembic |
| redis | MIT | https://github.com/redis/redis-py |
| nats-py | Apache-2.0 | https://github.com/nats-io/nats.py |
| nkeys | Apache-2.0 | https://github.com/nats-io/nkeys.py |
| temporalio | MIT | https://github.com/temporalio/sdk-python |
| protobuf | BSD-3-Clause | https://github.com/protocolbuffers/protobuf |
| pyeapi | BSD-3-Clause | https://github.com/arista-eosplus/pyeapi |
| netmiko | MIT | https://github.com/ktbyers/netmiko |
| macaddress | MIT | https://github.com/mentalisttraceur/python-macaddress |
| netaddr | BSD-3-Clause | https://github.com/netaddr/netaddr |
| paramiko | LGPL-2.1 | https://github.com/paramiko/paramiko |
| pynacl | Apache-2.0 | https://github.com/pyca/pynacl |
| cryptography | Apache-2.0/BSD-3-Clause | https://github.com/pyca/cryptography |
| PyJWT | MIT | https://github.com/jpadilla/pyjwt |
| jinja2 | BSD-3-Clause | https://github.com/pallets/jinja |
| ruamel-yaml | MIT | https://sourceforge.net/projects/ruamel-yaml/ |
| gitpython | BSD-3-Clause | https://github.com/gitpython-developers/GitPython |
| semver | BSD-3-Clause | https://github.com/python-semver/python-semver |
| numpy | BSD-3-Clause | https://github.com/numpy/numpy |
| pandas | BSD-3-Clause | https://github.com/pandas-dev/pandas |
| openpyxl | MIT | https://foss.heptapod.net/openpyxl/openpyxl |
| elasticsearch | Apache-2.0 | https://github.com/elastic/elasticsearch-py |
| requests-aws4auth | MIT | https://github.com/tedder/requests-aws4auth |
| brotli | MIT | https://github.com/google/brotli |
| slack-sdk | MIT | https://github.com/slackapi/python-slack-sdk |
| nv-air-sdk | MIT | https://pypi.org/project/nv-air-sdk/ |
| python-json-logger | BSD-2-Clause | https://github.com/madzak/python-json-logger |
| py-markdown-table | MIT | https://github.com/hvalev/py-markdown-table |
| mkdocs | BSD-2-Clause | https://github.com/mkdocs/mkdocs |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |

### Development Dependencies

| Package | License | URL |
|---------|---------|-----|
| pytest | MIT | https://github.com/pytest-dev/pytest |
| pytest-cov | MIT | https://github.com/pytest-dev/pytest-cov |
| pytest-html | MPL-2.0 | https://github.com/pytest-dev/pytest-html |
| pytest-asyncio | Apache-2.0 | https://github.com/pytest-dev/pytest-asyncio |
| pytest-mock | MIT | https://github.com/pytest-dev/pytest-mock |
| pytest-xdist | MIT | https://github.com/pytest-dev/pytest-xdist |
| pytest-aioresponses | MIT | https://github.com/pnuckowski/aioresponses |
| pytest-timeout | MIT | https://github.com/pytest-dev/pytest-timeout |
| testcontainers | Apache-2.0 | https://github.com/testcontainers/testcontainers-python |
| aiosqlite | MIT | https://github.com/omnilib/aiosqlite |
| responses | Apache-2.0 | https://github.com/getsentry/responses |
| mock | BSD-2-Clause | https://github.com/testing-cabal/mock |
| ruff | MIT | https://github.com/astral-sh/ruff |
| black | MIT | https://github.com/psf/black |
| isort | MIT | https://github.com/PyCQA/isort |
| mypy | MIT | https://github.com/python/mypy |
| ty | MIT | https://github.com/astral-sh/ty |
| flake8 | MIT | https://github.com/PyCQA/flake8 |
| flake8-bugbear | MIT | https://github.com/PyCQA/flake8-bugbear |
| pylint | GPL-2.0 | https://github.com/pylint-dev/pylint |
| pydocstyle | MIT | https://github.com/PyCQA/pydocstyle |
| bandit | Apache-2.0 | https://github.com/PyCQA/bandit |
| anybadge | MIT | https://github.com/jongracecox/anybadge |
| lxml | BSD-3-Clause | https://github.com/lxml/lxml |

---

## JavaScript/TypeScript Dependencies

The following npm packages are dependencies of the NVIDIA Config Manager UI. See `ui/package.json` for specific versions.

### Production Dependencies

| Package | License | URL |
|---------|---------|-----|
| @hookform/resolvers | MIT | https://github.com/react-hook-form/resolvers |
| @radix-ui/react-accordion | MIT | https://github.com/radix-ui/primitives |
| @radix-ui/react-checkbox | MIT | https://github.com/radix-ui/primitives |
| @radix-ui/react-dialog | MIT | https://github.com/radix-ui/primitives |
| @radix-ui/react-label | MIT | https://github.com/radix-ui/primitives |
| @radix-ui/react-navigation-menu | MIT | https://github.com/radix-ui/primitives |
| @radix-ui/react-popover | MIT | https://github.com/radix-ui/primitives |
| @radix-ui/react-scroll-area | MIT | https://github.com/radix-ui/primitives |
| @radix-ui/react-select | MIT | https://github.com/radix-ui/primitives |
| @radix-ui/react-slot | MIT | https://github.com/radix-ui/primitives |
| @radix-ui/react-switch | MIT | https://github.com/radix-ui/primitives |
| @radix-ui/react-tabs | MIT | https://github.com/radix-ui/primitives |
| @radix-ui/react-toast | MIT | https://github.com/radix-ui/primitives |
| @radix-ui/react-tooltip | MIT | https://github.com/radix-ui/primitives |
| @tanstack/match-sorter-utils | MIT | https://github.com/TanStack/table |
| @tanstack/react-table | MIT | https://github.com/TanStack/table |
| class-variance-authority | Apache-2.0 | https://github.com/joe-bell/cva |
| clsx | MIT | https://github.com/lukeed/clsx |
| cmdk | MIT | https://github.com/pacocoursey/cmdk |
| date-fns | MIT | https://github.com/date-fns/date-fns |
| jszip | MIT/GPL-3.0 | https://github.com/Stuk/jszip |
| lucide-react | ISC | https://github.com/lucide-icons/lucide |
| next | MIT | https://github.com/vercel/next.js |
| next-themes | MIT | https://github.com/pacocoursey/next-themes |
| nextjs-prometheus | MIT | https://github.com/jjrdn/nextjs-prometheus |
| prom-client | Apache-2.0 | https://github.com/siimon/prom-client |
| react | MIT | https://github.com/facebook/react |
| react-diff-view | MIT | https://github.com/otakustay/react-diff-view |
| react-dom | MIT | https://github.com/facebook/react |
| react-hook-form | MIT | https://github.com/react-hook-form/react-hook-form |
| react-markdown | MIT | https://github.com/remarkjs/react-markdown |
| react-resizable-panels | MIT | https://github.com/bvaughn/react-resizable-panels |
| react-syntax-highlighter | MIT | https://github.com/react-syntax-highlighter/react-syntax-highlighter |
| remark-gfm | MIT | https://github.com/remarkjs/remark-gfm |
| swr | MIT | https://github.com/vercel/swr |
| tailwind-merge | MIT | https://github.com/dcastil/tailwind-merge |
| tailwindcss-animate | MIT | https://github.com/jamiebuilds/tailwindcss-animate |
| zod | MIT | https://github.com/colinhacks/zod |

### Development Dependencies

| Package | License | URL |
|---------|---------|-----|
| @playwright/test | Apache-2.0 | https://github.com/microsoft/playwright |
| @types/node | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped |
| @types/react | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped |
| @types/react-dom | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped |
| eslint | MIT | https://github.com/eslint/eslint |
| eslint-config-next | MIT | https://github.com/vercel/next.js |
| msw | MIT | https://github.com/mswjs/msw |
| postcss | MIT | https://github.com/postcss/postcss |
| tailwindcss | MIT | https://github.com/tailwindlabs/tailwindcss |
| typescript | Apache-2.0 | https://github.com/microsoft/TypeScript |

---

## Go Dependencies

The following Go modules are dependencies of the nats-ready component. See `components/nats-ready/go.mod` for specific versions.

| Module | License | URL |
|--------|---------|-----|
| github.com/nats-io/nats.go | Apache-2.0 | https://github.com/nats-io/nats.go |
| github.com/rs/zerolog | MIT | https://github.com/rs/zerolog |
| github.com/klauspost/compress | BSD-3-Clause | https://github.com/klauspost/compress |
| github.com/mattn/go-colorable | MIT | https://github.com/mattn/go-colorable |
| github.com/mattn/go-isatty | MIT | https://github.com/mattn/go-isatty |
| github.com/nats-io/nkeys | Apache-2.0 | https://github.com/nats-io/nkeys |
| github.com/nats-io/nuid | Apache-2.0 | https://github.com/nats-io/nuid |
| golang.org/x/crypto | BSD-3-Clause | https://golang.org/x/crypto |
| golang.org/x/sys | BSD-3-Clause | https://golang.org/x/sys |

---

## Nautobot Component Dependencies

The following packages are dependencies of the Nautobot component (`components/nautobot`). See `components/nautobot/pyproject.toml` for specific versions.

| Package | License | URL |
|---------|---------|-----|
| nautobot | Apache-2.0 | https://github.com/nautobot/nautobot |
| nautobot-fsus | Apache-2.0 | https://github.com/NVIDIA/nautobot-app-fsus |
| nautobot-firewall-models | Apache-2.0 | https://github.com/nautobot/nautobot-app-firewall-models |
| nautobot-design-builder | Apache-2.0 | https://github.com/nautobot/nautobot-app-design-builder |
| nautobot-bgp-models | Apache-2.0 | https://github.com/nautobot/nautobot-app-bgp-models |
| pyasn1 | BSD-2-Clause | https://github.com/pyasn1/pyasn1 |

---

## Runtime Container Images

The following third-party container images may be deployed by NVIDIA Config
Manager. See `deploy/helm/values.yaml` for the configured versions.

| Component | Image | License | URL |
|-----------|-------|---------|-----|
| Redis Exporter | `docker.io/oliver006/redis_exporter` | MIT | https://github.com/oliver006/redis_exporter |

---

## Bundled CLI Tools

Air-gapped bundles can optionally include CLI tools copied from the build host. If a binary comes from an external package distribution, keep that distribution's notices with the bundle.

| Tool | License | URL |
|------|---------|-----|
| skopeo | Apache-2.0 | https://github.com/containers/skopeo |

---

## License Texts

### Redis Exporter — MIT License

```text
MIT License

Copyright (c) 2016 Oliver

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Apache License 2.0

```
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form.

      "Work" shall mean the work of authorship made available under the License.

      "Derivative Works" shall mean any work that is based on (or derived from)
      the Work.

      "Contribution" shall mean any work of authorship submitted for inclusion
      in the Work.

      "Contributor" shall mean Licensor and any Legal Entity on behalf of whom
      a Contribution has been received by Licensor.

   2. Grant of Copyright License.

   3. Grant of Patent License.

   4. Redistribution.

   5. Submission of Contributions.

   6. Trademarks.

   7. Disclaimer of Warranty.

   8. Limitation of Liability.

   9. Accepting Warranty or Additional Liability.

   END OF TERMS AND CONDITIONS

   See LICENSE file for the complete text.
```

### MIT License

```
MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### BSD 3-Clause License

```
BSD 3-Clause License

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### BSD 2-Clause License

```
BSD 2-Clause License

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### ISC License

```
ISC License

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
```

### LGPL-2.1 License

```
GNU Lesser General Public License v2.1

This library is free software; you can redistribute it and/or modify it under
the terms of the GNU Lesser General Public License as published by the Free
Software Foundation; either version 2.1 of the License, or (at your option)
any later version.

This library is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more
details.

See https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html for the full text.
```

### MPL-2.0 License

```
Mozilla Public License Version 2.0

This Source Code Form is subject to the terms of the Mozilla Public License,
v. 2.0. If a copy of the MPL was not distributed with this file, You can
obtain one at https://mozilla.org/MPL/2.0/.

See https://www.mozilla.org/en-US/MPL/2.0/ for the full text.
```

---

## Notes

1. **License Compatibility**: All dependencies listed above are compatible with the Apache 2.0 license under which NVIDIA Config Manager is distributed.

2. **Transitive Dependencies**: The packages listed here may have their own dependencies. The licenses for transitive dependencies are governed by their respective packages.

3. **Updates**: This document should be updated when dependencies are added, removed, or updated. Run package managers' license audit tools to verify license compliance:
   - Python: `pip-licenses` or `liccheck`
   - npm: `license-checker` or `npm audit`
   - Go: `go-licenses`

4. **LGPL Note**: Paramiko is licensed under LGPL-2.1. It is used as a library and not modified, which complies with LGPL terms. The LGPL allows linking to the library from Apache 2.0 licensed code.

5. **Runtime Components**: Some components listed in the NOTICE file (PostgreSQL, Redis, NATS, etc.) are infrastructure components that run separately and are not distributed as part of this project.
