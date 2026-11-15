# e2e: uptime-kuma

- result: **SUCCESS**
- commit: `c4f983a457e4ccb18e0836890b0dd80f11c2739a`
- host: windows, python 3.12.11
- duration: 70s, rtfmup exit code 0
- up line: Up: http://localhost:3001 (HTTP 200) in 5.2s. Ctrl+C to stop.
- answers given: variant=native run=node server/server.js component=-
- sandbox: npm global prefix and cache inside the scratch dir; stdin closed (high-risk steps declined)

## Steps (`.rtfmup/state.json`)

| step | status | exit |
|---|---|---|
| step-1 | ok | 0 |
| step-2 | ok | 0 |
| step-3 | skipped |  |
| step-4 | cancelled |  |
| step-5 | pending |  |

## Last 60 lines of output

```text
npm warn deprecated npmlog@5.0.1: This package is no longer supported.
npm warn deprecated lodash.get@4.4.2: This package is deprecated. Use the optional chaining (?.) operator instead.
npm warn deprecated inflight@1.0.6: This module is not supported, and leaks memory. Do not use it. Check out lru-cache if you want a good and tested way to coalesce async requests by a key value, which is much more comprehensive and powerful.
npm warn deprecated event-to-promise@0.7.0: Use promise-toolbox/fromEvent instead
npm warn deprecated glob@7.2.3: Old versions of glob are not supported, and contain widely publicized security vulnerabilities, which have been fixed in the current version. Please update. Support for old versions may be purchased (at exorbitant rates) by contacting i@izs.me
npm warn deprecated @npmcli/move-file@1.1.2: This functionality has been moved to @npmcli/fs
npm warn deprecated are-we-there-yet@2.0.0: This package is no longer supported.
npm warn deprecated npmlog@6.0.2: This package is no longer supported.
npm warn deprecated gauge@3.0.2: This package is no longer supported.
npm warn deprecated are-we-there-yet@3.0.1: This package is no longer supported.
npm warn deprecated @hey-api/client-fetch@0.10.2: Starting with v0.73.0, this package is bundled directly inside @hey-api/openapi-ts.
npm warn deprecated tar@6.2.1: Old versions of tar are not supported, and contain widely publicized security vulnerabilities, which have been fixed in the current version. Please update. Support for old versions may be purchased (at exorbitant rates) by contacting i@izs.me
npm warn deprecated gauge@4.0.4: This package is no longer supported.
npm warn deprecated prom-client@13.2.0: prom-client has been replaced by @prometheus-io/client
npm warn deprecated glob@10.3.16: Old versions of glob are not supported, and contain widely publicized security vulnerabilities, which have been fixed in the current version. Please update. Support for old versions may be purchased (at exorbitant rates) by contacting i@izs.me

added 649 packages in 32s

113 packages are looking for funding
  run `npm fund` for details

> uptime-kuma@2.5.9 download-dist
> node extra/download-dist.js

Downloading dist
https://github.com/louislam/uptime-kuma/releases/download/2.5.9/dist.tar.gz
https://release-assets.githubusercontent.com/github-production-release-asset/382496361/59932977-3aa6-45e5-9456-0907939bee7b?sp=r&sv=2018-11-09&sr=b&spr=https&se=2026-11-15T05%3A27%3A14Z&rscd=attachment%3B+filename%3Ddist.tar.gz&rsct=application%2Foctet-stream&skoid=96c2d410-5711-43a1-aedd-ab1947aa7ab0&sktid=398a6654-997b-47e9-b12b-9515b896b4de&skt=2026-11-15T04%3A26%3A57Z&ske=2026-11-15T05%3A27%3A14Z&sks=b&skv=2018-11-09&sig=fN7hkuWyg6f9lt4C6eoaHayqc0evH5GeDpJFt1L81Pk%3D&jwt=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmVsZWFzZS1hc3NldHMuZ2l0aHVidXNlcmNvbnRlbnQuY29tIiwia2V5Ijoia2V5MSIsImV4cCI6MTc5NDcxODAxNywibmJmIjoxNzk0NzE3NzE3LCJwYXRoIjoicmVsZWFzZWFzc2V0cHJvZHVjdGlvbi5ibG9iLmNvcmUud2luZG93cy5uZXQifQ.V49A5kgwcZm6SoYzlP7Km2t-bhdVL1UBHRX7fYZUZy0&response-content-disposition=attachment%3B%20filename%3Ddist.tar.gz&response-content-type=application%2Foctet-stream
Extracting dist...
Done
done (exit 0) in 35.1s
HIGH RISK step: pm2 startup && pm2 save
  command: pm2 startup && pm2 save
  why:     changes a system service
  why:     needs a shell (pipes, chaining or redirects)
  skipped: --yes never approves high-risk steps.
[4/4] run � node server/server.js
Welcome to Uptime Kuma
Your Node.js version: 26.13.1
[36m2026-11-14T23:41:58-05:00[0m [[32mSERVER[0m] [36mINFO:[0m Env: production
[36m2026-11-14T23:41:59-05:00[0m [[32mSERVER[0m] [36mINFO:[0m Uptime Kuma Version: 2.5.9
[36m2026-11-14T23:41:59-05:00[0m [[32mSERVER[0m] [36mINFO:[0m Loading modules
[36m2026-11-14T23:42:02-05:00[0m [[32mSERVER[0m] [36mINFO:[0m Creating express and socket.io instance
[36m2026-11-14T23:42:02-05:00[0m [[32mSERVER[0m] [36mINFO:[0m Server Type: HTTP
[36m2026-11-14T23:42:03-05:00[0m [[32mSERVER[0m] [36mINFO:[0m Data Dir: ./data/
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m db-config.json is not found or invalid: ENOENT: no such file or directory, open 'C:\Users\drew\AppData\Local\rtfmup\Cache\bench\e2e\uptime-kuma-20261114-234055\uptime-kuma\data\db-config.json'
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m Starting Setup Database
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m Listening on:
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m -  http://localhost:3001
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m -  http://172.30.48.1:3001
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m -  http://192.168.0.2:3001
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m -  http://[2600:1701:441:13cf:24fa:6b5c:5f0e:c3e1]:3001
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m -  http://[2600:1701:441:13cf:7cef:8b64:d24f:5881]:3001
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m -  http://[fd37:e9fe:ae9a:1:7cef:8b64:d24f:5881]:3001
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m -  http://[fd37:e9fe:ae9a:1:8c24:a800:ce1a:b061]:3001
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m -  http://[fe80::1325:e161:bfaf:fe8a]:3001
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m -  http://[fe80::edaa:befd:ff9f:2637]:3001
[36m2026-11-14T23:42:03-05:00[0m [[32mSETUP-DATABASE[0m] [36mINFO:[0m Waiting for user action...
Up: http://localhost:3001 (HTTP 200) in 5.2s. Ctrl+C to stop.
cancelled (exit 3221225786) in 7.5s
Stopped.
```
