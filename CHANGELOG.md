# Changelog

## [1.0.1](https://github.com/g4bri3lDev/kaco-rs485/compare/v1.0.0...v1.0.1) (2026-09-12)


### Documentation

* _discard_stale is not what stops a stale frame being misread ([f8ba046](https://github.com/g4bri3lDev/kaco-rs485/commit/f8ba04618d5d6d34f3f739d7584d0839db43ffc1))


### Refactoring

* fold the local test doubles onto the shipped one ([5dc23c2](https://github.com/g4bri3lDev/kaco-rs485/commit/5dc23c27ed84d0e81d0d6e41f8d67298035c364e))

## [1.0.0](https://github.com/g4bri3lDev/kaco-rs485/compare/v0.5.0...v1.0.0) (2026-09-12)


### ⚠ BREAKING CHANGES

* probe the bus cheaply first, and re-probe when the silences are suspect

### Features

* probe the bus cheaply first, and re-probe when the silences are suspect ([696b523](https://github.com/g4bri3lDev/kaco-rs485/commit/696b523162e4ceaf70ea713841a1a5220c12aac2))

## [0.5.0](https://github.com/g4bri3lDev/kaco-rs485/compare/v0.4.0...v0.5.0) (2026-09-10)


### Features

* move consumer-side derivations into the library ([29b1776](https://github.com/g4bri3lDev/kaco-rs485/commit/29b177691fb58e94112e809ef6a0703aea9b76fa))
* record per-inverter reply latency on InverterState ([b2cea9a](https://github.com/g4bri3lDev/kaco-rs485/commit/b2cea9a5873baa4991303975bab7d5e7865a4b67))

## [0.4.0](https://github.com/g4bri3lDev/kaco-rs485/compare/v0.3.0...v0.4.0) (2026-09-10)


### Features

* add kaco_rs485.testing, doubles built from captured frames ([9d1f07b](https://github.com/g4bri3lDev/kaco-rs485/commit/9d1f07b5dfd0a43dfa5b4d5ee75926cd323ee872))
* add stable status slugs for consumers with a fixed vocabulary ([11a64ac](https://github.com/g4bri3lDev/kaco-rs485/commit/11a64ac5b47063f5d30c6a526af9cfefd5786586))
* ship py.typed so consumers can see our types ([ae084dc](https://github.com/g4bri3lDev/kaco-rs485/commit/ae084dc1863cf98405635f21c39d6a2a1c36e72e))


### Documentation

* explain why serialx is a range and never a pin ([47f902d](https://github.com/g4bri3lDev/kaco-rs485/commit/47f902d5fa8868519d0a8f574d8f1a6b022fc4bb))
* xi units do report status 15, for about two minutes ([#8](https://github.com/g4bri3lDev/kaco-rs485/issues/8)) ([46c3338](https://github.com/g4bri3lDev/kaco-rs485/commit/46c3338d801b8e195622165a8fe7b194311e28c9))

## [0.3.0](https://github.com/g4bri3lDev/kaco-rs485/compare/v0.2.0...v0.3.0) (2026-08-30)


### Features

* capture firmware during discovery ([#7](https://github.com/g4bri3lDev/kaco-rs485/issues/7)) ([c66ffc8](https://github.com/g4bri3lDev/kaco-rs485/commit/c66ffc84f90b4bacf7ed9e01dfa8113774d983d5))


### Documentation

* reconcile framing's timing claims with what was measured ([#6](https://github.com/g4bri3lDev/kaco-rs485/issues/6)) ([57f2a8a](https://github.com/g4bri3lDev/kaco-rs485/commit/57f2a8a2efc8af7695c3eb75967fc6d298dfcc43))
* record what the units actually do at dusk ([#4](https://github.com/g4bri3lDev/kaco-rs485/issues/4)) ([1b1df6a](https://github.com/g4bri3lDev/kaco-rs485/commit/1b1df6ac090b03b5153c6095206503d426ea9c3a))

## [0.2.0](https://github.com/g4bri3lDev/kaco-rs485/compare/v0.1.0...v0.2.0) (2026-08-30)


### Features

* read the firmware version once per address ([#3](https://github.com/g4bri3lDev/kaco-rs485/issues/3)) ([cf5ec40](https://github.com/g4bri3lDev/kaco-rs485/commit/cf5ec406301c36d4b82b099422cf7f5779ef7388))


### Documentation

* point at the ready-made ESPHome RS485 proxy ([f48978c](https://github.com/g4bri3lDev/kaco-rs485/commit/f48978c0f6626dd665d850ecfc49a53829d962dc))

## 0.1.0 (2026-08-25)


### Features

* accept third-party captures as test fixtures ([e7c9ea8](https://github.com/g4bri3lDev/kaco-rs485/commit/e7c9ea8aceb3eff0bbfd2ed3df35b0fe90713785))
* bus discovery instead of hardcoded addresses ([95ac5d1](https://github.com/g4bri3lDev/kaco-rs485/commit/95ac5d1f288641ee3101313662decac1c4b5bdc4))
* fold in findings from the vendor firmware analysis ([e1c782a](https://github.com/g4bri3lDev/kaco-rs485/commit/e1c782ac9091267a160641327a5b214e4cc12140))
* KACO xi RS485 library with URL-addressed transport ([ab6e9f5](https://github.com/g4bri3lDev/kaco-rs485/commit/ab6e9f5d3fa6c7705e93b349c19390742a1ca27f))
* status code table with the two field-observed codes ([7c5efe4](https://github.com/g4bri3lDev/kaco-rs485/commit/7c5efe4d106f66f96eb2cd1b5dffcdb1563b736b))


### Bug fixes

* an empty listen is not evidence of a fault ([b7a5112](https://github.com/g4bri3lDev/kaco-rs485/commit/b7a51124939e692e06d10df4ddad2cf04afa543e))
* connection loss must not kill a long-running poll ([54ff92f](https://github.com/g4bri3lDev/kaco-rs485/commit/54ff92f9769a53942e26ea993bc23a390eb89bcc))
* narrow two types the checker could not follow ([7706eef](https://github.com/g4bri3lDev/kaco-rs485/commit/7706eef5b993a86e433719fe09e430d675a279de))
* pace scan and sweep, they were polling back to back ([eb6bc7f](https://github.com/g4bri3lDev/kaco-rs485/commit/eb6bc7f50e579f16e9f021d51a7d5c28f871cb48))
* remove installation-specific assumptions; instrument timings ([1296848](https://github.com/g4bri3lDev/kaco-rs485/commit/129684876d49d9700edc5dca1177095b248b5bff))


### Performance

* only pay the bus-settle gap after an address replies ([d2b008e](https://github.com/g4bri3lDev/kaco-rs485/commit/d2b008ee81765e976a2ee5f2472d368d140fc239))
