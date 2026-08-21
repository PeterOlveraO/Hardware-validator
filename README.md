# Hardware Validator

Hardware Validator is a Python 3.11+ application for printing a local Linux
hardware inventory. It reads standard Linux files and APIs without benchmarks,
stress, destructive operations, root requirements, or external applications.

## Install

Create a clean virtual environment and install the project:

```text
python -m venv .venv
.venv/bin/python -m pip install -e .
```

## Run

```text
.venv/bin/python -m hardware_validator
```

The installed console script is also available as
`.venv/bin/hardware-validator`.

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

## Verify

```text
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
.venv/bin/python -c "import importlib, pkgutil, hardware_validator; [importlib.import_module(module.name) for module in pkgutil.walk_packages(hardware_validator.__path__, hardware_validator.__name__ + '.')]"
```

The product does not invoke system applications, require root, or access remote
services at runtime.
