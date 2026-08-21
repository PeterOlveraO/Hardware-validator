# Hardware Validator

Hardware Validator is a Python 3.11+ application for printing a local Linux
hardware inventory. It reads standard Linux files and APIs without benchmarks,
stress, destructive operations, root requirements, or external applications.
The inventory includes local network-interface configuration but performs no
network connections, scans, probes, or remote lookups.

## Install

Create a clean virtual environment and install the project:

```text
python -m venv .venv
.venv/bin/python -m pip install -e .
```

## Run

The normal command prints a short inventory intended for everyday use:

```text
.venv/bin/python -m hardware_validator
.venv/bin/hardware-validator
```

Use `--verbose` for the complete technical report:

```text
.venv/bin/python -m hardware_validator --verbose
.venv/bin/hardware-validator --verbose
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

## Memory values

RAM total, available, and free are one observation from
`psutil.virtual_memory()`. RAM used is calculated as `total - available` because
Linux available memory includes reclaimable memory and is more useful than free
memory alone. Swap available is the reported free swap, and swap used is checked
against `total - available`. A system with zero swap is valid.

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
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
.venv/bin/python -c "import importlib, pkgutil, hardware_validator; [importlib.import_module(module.name) for module in pkgutil.walk_packages(hardware_validator.__path__, hardware_validator.__name__ + '.')]"
```

The product does not invoke system applications, require root, or access remote
services at runtime.
