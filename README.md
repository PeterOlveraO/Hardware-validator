# Hardware Validator

Hardware Validator is a Linux command-line tool written in Rust. It prints a
local hardware inventory and, only when explicitly requested, runs a moderate
CPU load exercise. Inventory reads standard Linux files and system calls
without benchmarks, load, destructive operations, root requirements, or
external applications. The inventory includes local network-interface
configuration but performs no network connections, scans, probes, or remote
lookups.

The program is a single binary: its only crate dependency is
`libc`, and it needs no runtime, virtual environment, or Python packages.

## Build

Install a stable Rust toolchain (1.85 or newer) on Linux, then build the
release binary:

```text
cargo build --release
```

The binary is `target/release/hardware-validator`. Copy it anywhere on the
`PATH`, or install it for the current user:

```text
cargo install --path .
```

Hardware Validator supports Linux only; building for another operating system
stops with a compile error.

## Run

The normal command prints a short inventory intended for everyday use:

```text
hardware-validator
```

Use `--verbose` for the complete technical report:

```text
hardware-validator --verbose
```

The technical report can be saved with standard output redirection. Fatal and
operational error messages remain on stderr and can be redirected separately:

```text
hardware-validator --verbose > hardware-details.txt
hardware-validator --verbose 2> hardware-validator.log
```

`hardware-details.txt` is a technical inventory report, not a log. The log path
in the second example receives only errors or operational messages.

The summary shows only physical Ethernet and Wi-Fi interfaces with connection
state, known positive speed, local IPv4 addresses and masks, MAC address, an
associated IPv4 default gateway, and system-wide configured DNS. The technical
report additionally includes loopback, virtual and unknown interfaces, IPv6
prefixes, IPv4/IPv6 default gateways with interface and metric, upstream DNS when
locally available, MTU, and duplex. Neither mode performs network traffic.

Basic command information is available with `--help`; the installed version is
available with `--version`.

## Data sources

| Component | Sources |
|-----------|---------|
| System | `/etc/os-release` (or `/usr/lib/os-release`), `uname`, `/sys/class/dmi/id`, Device Tree model |
| CPU | `/proc/cpuinfo`, `/sys/devices/system/cpu` topology, cpufreq and caches, `sysconf` |
| Memory | `/proc/meminfo`, EDAC sysfs |
| Storage | `/sys/class/block`, `/proc/self/mounts`, `/proc/filesystems`, `statvfs` |
| GPU | `/sys/class/drm`, `/sys/bus/pci/devices` |
| Network | `/sys/class/net`, `getifaddrs`, `/proc/net/route`, `/proc/net/ipv6_route`, `resolv.conf` |
| CPU test | `/proc/stat`, cpufreq, `/sys/class/hwmon`, `/sys/class/thermal` |

Unavailable or unreadable values are reported as `Unknown`; the program never
guesses hardware data.

## CPU load test

The CPU test starts only through an explicit command. Running the inventory alone
never creates load workers:

```text
hardware-validator test cpu
```

Defaults:

- Duration: 10 minutes.
- Requested worker duty cycle: 60%.
- Configurable program thermal safeguard: 85 C.
- One worker thread per logical processor.

Options:

```text
hardware-validator test cpu --duration 10m
hardware-validator test cpu --load 60
hardware-validator test cpu --max-temperature 85
hardware-validator test cpu --allow-no-temperature
hardware-validator test cpu --verbose
```

Duration accepts integer seconds, minutes, or hours with `s`, `m`, or `h`, from
10 seconds through 24 hours. Load accepts 10 through 90; 100% is not supported.
The requested load is an approximate worker duty cycle over 100 ms cycles, not a
guarantee of total system utilization. Other applications can increase measured
utilization.

The default 85 C value is a configurable Hardware Validator safeguard, not an
official limit for every processor. When a monitored sensor provides a reliable
lower critical limit, the lower value is used. The program does not claim that an
unattributed sensor measures the CPU directly. Sensors come from hwmon
(identified as `hwmon:<chip>:<label>:<occurrence>`); thermal zones
(`thermal:<zone>`) are used only when hwmon offers none.

By default, no load starts without a usable temperature measurement.
`--allow-no-temperature` overrides that protection and prints a clear warning to
stderr. It should be used only when the operator accepts that Hardware Validator
cannot provide thermal protection for that run.

Every load test generates heat. Stop with `Ctrl+C` if you observe unusual odor,
noise, defective fans, shutdowns, or other abnormal behavior. Ctrl+C requests
cooperative worker shutdown within one cycle and returns exit code 130. The final
result reports only what happened during the configured exercise; it does not
certify CPU health, reliability, cooling adequacy, or remaining life.

Exit codes:

- `0`: configured duration completed.
- `1`: thermal limit, lost sensors, worker failure, SIGTERM, or operational error.
- `2`: invalid arguments.
- `130`: interrupted with Ctrl+C.

Normal progress is compact. `--verbose` adds approximately one-second CPU,
frequency, sensor, and worker details. Configuration, progress, and the final
summary use stdout. Warnings and errors are emitted to stderr when they occur;
the final stdout summary also retains its warning list. Standard redirection works:

```text
hardware-validator test cpu > cpu-test.txt
hardware-validator test cpu --verbose > cpu-test-details.txt
hardware-validator test cpu 2> cpu-test-errors.log
```

The test creates no report or log automatically, requires no root access, and
does not invoke `stress`, `stress-ng`, `sysbench`, `sensors`, or any remote
service. It performs no network connection or storage workload and does not
change CPU affinity, priority, governor, or frequency.

## Memory values

RAM total, available, and free are one observation of `/proc/meminfo`. RAM used
is calculated as `total - available` because Linux available memory includes
reclaimable memory and is more useful than free memory alone. Swap available is
the free swap, and swap used is `total - available`. A system with zero swap is
valid.

Models retain capacities in bytes and CPU frequencies in hertz. The terminal
report alone converts them to IEC capacity units and readable frequency units.
Block capacities reported by Linux sysfs as 512-byte sectors are calculated as
`sector_count * 512`.

## Privacy

A MAC address identifies an interface relatively consistently. A private IPv4
address reveals part of the local network structure, and a global IPv6 address
may be publicly routable. Subnet masks, default gateways, and internal DNS
servers reveal additional local network structure and configuration. Hide
hostnames, hardware serials, IP addresses, MAC addresses, masks, gateways, and
DNS servers before publishing screenshots or technical reports.

Hardware Validator does not log, transmit, upload, or query this information
outside the local computer.

## Verify

```text
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
```

The tests build throwaway procfs and sysfs trees and a scripted CPU-test
environment, so they do not depend on the developer's hardware. GitHub Actions
runs the same checks on Linux for every push, then runs the inventory and a
short CPU test on the runner.

The product does not invoke system applications, require root, or access remote
services at runtime.

## History

Versions 0.1.0 and 0.2.0 were written in Python with `psutil`. Version 0.3.0 is
a Rust port with the same commands, options, report layout, and exit codes.
Differences from 0.2.0:

- No Python, virtual environment, or `psutil` version pin is required.
- CPU load workers are native threads instead of spawned Python processes.
- Temperature sensor identities start with `hwmon:` instead of `psutil:`.
- The verbose report omits fields Linux never provided: the GPU "Primary" flag
  and the EDAC module manufacturer, part number, serial number, and speed.

The specifications in `docs/` and the implementation and integration reports
describe the Python versions and are kept for reference.
