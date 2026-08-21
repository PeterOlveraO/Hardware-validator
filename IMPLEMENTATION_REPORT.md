# Informe de implementación de Hardware Validator

Fecha de verificación: 20 de agosto de 2026.

## Estado general

Se implementó un inventario informativo, local, no interactivo y no destructivo
para Linux. La ejecución normal detecta sistema, CPU, memoria, almacenamiento,
GPU y red local, conserva los valores desconocidos de forma explícita y genera
un reporte de terminal estable.

Los encargos posteriores del usuario amplían expresamente el alcance histórico
de `docs/v0.1.0-specification.md`. Por ese motivo la implementación actual sí
incluye distribución, kernel, hostname, flags de CPU, swap, particiones,
montajes, conectores GPU y salida final, aunque ese documento anterior los
excluyera.

## Metas cumplidas

- [x] Detección de distribución, versión, kernel, arquitectura y hostname.
- [x] Detección de equipo, placa base y BIOS o firmware mediante interfaces
  locales disponibles.
- [x] Detección x86 y ARM de identidad, topología, frecuencias, cachés y
  capacidades de CPU.
- [x] Separación entre procesadores lógicos, núcleos físicos y paquetes físicos.
- [x] Detección de RAM actual y swap sin reservar memoria para probarla.
- [x] Detección opcional de módulos mediante EDAC sysfs.
- [x] Inventario de dispositivos de bloque, particiones, sistemas de archivos,
  montajes y uso accesible.
- [x] Clasificación conservadora de almacenamiento y conservación de
  dispositivos desconocidos.
- [x] Inventario de cero, una o varias GPUs combinando DRM y PCI sin duplicar
  conectores como GPUs.
- [x] Inventario local de interfaces Ethernet, Wi-Fi, loopback, virtuales y
  desconocidas sin tráfico de red.
- [x] Máscaras y prefijos locales, gateways predeterminados IPv4/IPv6 y DNS
  configurado o ascendente mediante archivos locales de Linux.
- [x] Salida de terminal con secciones, unidades legibles, `Unknown`, `--help` y
  `--version`.
- [x] Estados internos `complete`, `partial` y `unavailable` sin presentarlos
  como salud, diagnóstico, PASS o FAIL.
- [x] Pruebas simuladas independientes del hardware del desarrollador.
- [x] Ejecución real sin root.

## Campos y fuentes

### Sistema, placa base y firmware

Campos:

- Distribución y versión.
- Kernel, arquitectura y hostname.
- Fabricante y modelo del equipo.
- Fabricante, modelo y versión de placa base.
- Fabricante, versión y fecha de BIOS o firmware.

Fuentes:

- `/etc/os-release`, con fallback estándar a `/usr/lib/os-release`.
- `os.uname()` de Python, sin ejecutar el comando `uname`.
- `/sys/class/dmi/id/*`.
- `/sys/firmware/devicetree/base/model` como fallback exclusivo del modelo del
  equipo.

Los placeholders DMI habituales, cadenas vacías y campos ausentes se convierten
en `None`.

### CPU

Campos:

- Nombres o identificadores de modelo, conservando orden y valores distintos.
- Fabricantes o implementadores.
- Arquitectura de máquina.
- Paquetes físicos cuando toda la topología presente es legible.
- Núcleos físicos y procesadores lógicos por separado.
- Frecuencia actual media y frecuencia máxima informada.
- Cachés por nivel, tipo y capacidad.
- Capacidades o flags sin duplicados.

Fuentes:

- `/proc/cpuinfo` para identidad y capacidades x86 o ARM.
- `os.uname().machine` para arquitectura.
- `psutil.cpu_count(logical=True/False)` para recuentos lógico y físico.
- `/sys/devices/system/cpu/present` y
  `cpu*/topology/physical_package_id` para paquetes.
- `psutil.cpu_freq(percpu=True)` para frecuencias.
- `cpu*/cache/index*` en sysfs para cachés.

Las frecuencias positivas en MHz se convierten a Hz multiplicando por
`1_000_000`. La frecuencia actual es la media aritmética redondeada de las
observaciones positivas. No se sustituye un núcleo físico desconocido por el
número de hilos lógicos.

### Memoria y swap

Campos:

- RAM total, disponible, utilizada y libre.
- Swap total, disponible y utilizada, incluido el caso válido de cero swap.
- Módulos EDAC con ubicación, tipo y capacidad cuando estén expuestos.

Fuentes:

- `psutil.virtual_memory()`.
- `psutil.swap_memory()`.
- `/sys/devices/system/edac/mc/mc*/dimm*`.

Fórmulas y unidades:

- RAM utilizada: `total - disponible`.
- Swap disponible: campo `free` informado por psutil.
- Swap utilizada se valida contra `total - disponible`.
- El archivo EDAC `size`, documentado en MiB, se convierte mediante
  `valor * 1024**2`.
- Los modelos conservan bytes; la conversión a KiB, MiB, GiB, etc. ocurre solo
  en presentación.

### Almacenamiento

Campos:

- Dispositivos de bloque y ruta `/dev` únicamente cuando el nodo existe.
- Fabricante, modelo, serial, capacidad, transporte, solo lectura, removible,
  rotacional y sectores lógico/físico.
- Clasificación conservadora: HDD, SSD, NVMe, eMMC, removible, virtual o
  desconocida.
- Particiones y relación canónica con el dispositivo padre.
- Sistema de archivos, puntos de montaje y espacio total, usado y disponible.

Fuentes:

- `/sys/class/block` y los destinos canónicos bajo `/sys/devices`.
- Atributos `size`, `ro`, `removable`, `queue/rotational`, tamaños de bloque y
  metadatos bajo `device/`.
- Enlaces `subsystem`, protocolos explícitos y ancestros sysfs para transporte.
- `psutil.disk_partitions(all=False)` y `psutil.disk_usage()`.

La capacidad sysfs se calcula como `sectores * 512`, según la ABI de bloque de
Linux. No se abre ningún nodo de bloque. El uso no se consulta para NFS, CIFS,
SMB, FUSE o autofs, evitando activar automontajes o servicios remotos.

### GPU

Campos:

- Cero, una o varias GPUs.
- Nombre comercial fiable cuando `amdgpu` expone `product_name`.
- `vendor_id` y `device_id` canónicos como fallback permanente.
- Driver, dirección PCI, memoria VRAM amdgpu y conectores DRM.
- Evidencia explícita de GPU dedicada mediante `board_info` CEM/OAM.
- Estado `boot_vga` separado del concepto más amplio de GPU primaria.

Fuentes:

- `/sys/class/drm`.
- `/sys/bus/pci/devices` y clase PCI de pantalla.
- Enlaces canónicos `device` y `driver`.
- Atributos documentados `product_name`, `board_info`,
  `mem_info_vram_total` y `boot_vga` cuando corresponden.

Los nodos DRM se fusionan con PCI mediante la identidad canónica del dispositivo.
Los conectores se asocian por la misma identidad y nunca crean GPUs adicionales.

### Red local

Campos:

- Nombre y tipo conservador de interfaz.
- Estado activo o inactivo, MAC, IPv4 e IPv6 locales con máscara o prefijo.
- Velocidad positiva, MTU y dúplex cuando están disponibles.
- Gateways predeterminados IPv4/IPv6 con interfaz y métrica.
- DNS configurado y DNS ascendente opcional, conservados por separado.

Fuentes:

- `psutil.net_if_addrs()` y `psutil.net_if_stats()`.
- `/sys/class/net`, tipo ARPHRD, marcadores inalámbricos, identidad canónica y
  enlace de dispositivo físico.
- `/proc/net/route` y `/proc/net/ipv6_route` para rutas predeterminadas.
- `/etc/resolv.conf` y, cuando existe,
  `/run/systemd/resolve/resolv.conf` para DNS local.

No se usan prefijos de nombres para clasificar y no se realizan conexiones,
escaneos, DNS, captura, medición ni consultas remotas.

## Presentación

La salida predeterminada es un resumen con secciones `Computer`, `Processor`,
`Memory`, `Storage`, `Graphics` y `Network`. `--verbose` conserva el reporte
técnico con las secciones `System`, `CPU`, `Memory`, `Storage`, `GPU` y
`Network`. Ambos consumen el mismo inventario ya detectado. Los modelos mantienen
unidades base y los formateadores convierten:

- Bytes a unidades IEC: KiB, MiB, GiB, TiB, PiB o EiB.
- Hertz a kHz, MHz, GHz o THz.
- Booleanos a `Yes` o `No`.
- Escalares ausentes a `Unknown`.
- Colecciones vacías verificadas a `None detected`.
- Colecciones no verificables a `Unknown`.

El formateador no detecta hardware. Los caracteres de control y de formato
Unicode se escapan para no emitir secuencias de terminal inesperadas.

Ejemplo abreviado:

```text
System
  Distribution: Example Linux
  Architecture: x86_64

CPU
  Physical Cores: 8
  Logical Processors: 16

Memory
  Total: 16 GiB
  Used: 7.5 GiB

Storage
  Disks:
    Disk 1:
      Kind: nvme

GPU
  Devices: None detected
```

## Pruebas

Suite final: 139 pruebas unitarias y de integración aprobadas.

Cobertura destacada:

- CPU x86, ARM, múltiples paquetes, topología incompleta, cachés duplicadas y
  frecuencias conocidas.
- Sistema completo, Device Tree, fallback de os-release, datos ausentes,
  ilegibles y malformados.
- RAM y swap completos, cero swap, unidades EDAC, módulos parciales y datos
  psutil malformados.
- SATA HDD/SSD, NVMe, eMMC, removible, virtual, desconocido, particiones,
  montajes con espacios o escapes, permisos y uso imposible.
- iGPU sin clasificación inferida, GPU dedicada con evidencia, múltiples GPUs,
  IDs sin nombre, conectores, fuentes ausentes y permisos.
- Snapshots completos y vacíos, múltiples dispositivos, unidades, valores cero,
  cadenas largas, controles de terminal y enteros muy grandes.
- Interfaces físicas, virtuales y desconocidas; máscaras válidas o malformadas;
  rutas predeterminadas IPv4/IPv6, endianess, métricas, flags y filas truncadas;
  DNS configurado, stub y ascendente.
- Dependencia ausente o incompatible, `--help`, `--version`, errores de escritura
  y violaciones internas de contrato.
- Integración de los seis detectores, fallo operativo independiente de cada
  componente, combinaciones parciales y errores de programación fatales.

Comandos ejecutados:

```text
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m hardware_validator --help
.venv/bin/python -m hardware_validator --version
.venv/bin/python -m hardware_validator
.venv/bin/python -m hardware_validator --verbose
.venv/bin/python -m pip check
```

## Ejecución real sin root

Los seis detectores se ejecutaron en el host Linux real sin `sudo` y terminaron
con estado interno `complete` y sin incidencias. La salida real detectó sistema,
CPU, RAM/swap, un NVMe con particiones y montajes, y una GPU Intel mediante IDs y
driver. La red detectó dos interfaces; las direcciones, máscaras, gateways, MAC y
DNS se descartaron durante la verificación para no publicarlos.

La memoria se verificó con `/proc/self/status`:

- Proceso Python base observado: aproximadamente 15.9 MiB de `VmHWM`.
- Inventario completo observado: aproximadamente 22.9 MiB de `VmHWM`.
- Incremento aproximado: 7 MiB.

No se reservaron bloques grandes de RAM ni se generó carga deliberada.

## Seguridad y operaciones excluidas

La implementación no:

- Ejecuta `lscpu`, `dmidecode`, `free`, `lsblk`, `blkid`, `smartctl`, `df`,
  `lspci`, herramientas gráficas ni otros procesos externos.
- Usa subprocesses, shell, red o servicios remotos.
- Abre `/dev/sdX`, `/dev/nvmeX` u otros dispositivos de bloque.
- Escribe en discos para probarlos.
- Realiza SMART, benchmarks, estrés, renderizado o cálculos GPU.
- Asigna RAM para pruebas de integridad.
- Presenta estados PASS, FAIL, WARNING, puntuaciones o `test_status`.

Los archivos temporales usados por las pruebas son exclusivamente árboles sysfs
simulados dentro de directorios temporales; el código de producción no los crea.

## Incidencias encontradas y resueltas

- `/usr/bin/time` no existe en el host. La medición se sustituyó por las fuentes
  estándar `/proc/self/status` y `resource`; se descartó `ru_maxrss` porque su
  unidad en este intérprete no coincidía con la esperada.
- La primera revisión detectó una clasificación SSD/HDD demasiado amplia. Se
  restringió a evidencia conjunta de transporte, tipo y rotación.
- Se eliminó la asociación de particiones por simple basename para evitar falsos
  positivos.
- Se evitó consultar uso de montajes remotos, FUSE y autofs.
- Se impidió crear GPUs vacías a partir de tarjetas DRM sin dispositivo canónico.
- La lectura de VRAM se limitó al ABI documentado de `amdgpu`.
- Se separó `boot_vga` de la noción ambigua de GPU primaria.
- Se añadió fallback `/usr/lib/os-release` y estado parcial para topología CPU
  incompleta.
- Los errores de invariantes de modelos ahora atraviesan el límite de recuperación
  como errores internos en vez de convertirse en datos no disponibles.
- Se endureció el formateo para controles Unicode y valores enteros extremos.
- Se endurecieron rutas y DNS para que datos malformados sin candidatos válidos
  permanezcan desconocidos en vez de presentarse como ausencia confirmada.
- La decodificación de gateways IPv4 respeta el endianess nativo y las rutas
  rechazadas o con gateway nulo no se presentan.

No quedan errores de pruebas conocidos.

## Limitaciones y datos que pueden quedar desconocidos

- DMI, Device Tree, EDAC, DRM o PCI pueden estar ausentes o protegidos; los campos
  correspondientes permanecen `Unknown` sin cerrar la aplicación.
- EDAC normalmente no expone fabricante, número de parte, serial o velocidad del
  módulo. No se implementó un parser SMBIOS Type 17; esos campos suelen quedar
  desconocidos sin una interfaz adicional fiable y legible.
- En ARM, un `CPU part` puede conservarse como identificador de modelo, pero no se
  traduce a un nombre comercial mediante tablas incompletas.
- La frecuencia máxima de psutil puede reflejar el máximo vigente de la política
  cpufreq, no necesariamente una especificación comercial inmutable.
- Los aliases `/dev/disk/by-*` y `/dev/mapper/*` no se resuelven a major/minor;
  por ello algunos montajes mediante alias pueden no asociarse a una partición,
  aunque siguen apareciendo en la lista de montajes.
- La clasificación HDD/SSD permanece desconocida cuando transporte, tipo o
  rotación no constituyen evidencia suficiente.
- El espacio de montajes remotos, FUSE y autofs se deja desconocido por seguridad.
- El nombre comercial GPU solo se muestra desde interfaces fiables implementadas;
  no se incluye una base PCI ni se inventa a partir de IDs.
- `integrated` normalmente queda desconocido porque no se infiere por fabricante,
  driver, memoria o dirección. `dedicated` solo se establece con evidencia CEM/OAM
  documentada por amdgpu.
- `is_primary` permanece desconocido cuando no existe una fuente inequívoca.
  `is_boot_vga` muestra por separado la selección de firmware PCI.
- Las rutas IPv6 con selector de origen no se presentan como gateways globales,
  porque el modelo público no representa selectores de policy routing.
- El DNS ascendente queda desconocido cuando systemd-resolved no publica su
  archivo opcional; el DNS configurado en `/etc/resolv.conf` se conserva aparte.

## Archivos principales

- `pyproject.toml`: paquete, script y dependencia exacta.
- `src/hardware_validator/models.py`: modelos públicos inmutables.
- `src/hardware_validator/collector.py`: contratos y aislamiento de detectores.
- `src/hardware_validator/detectors/system.py`: sistema, placa y firmware.
- `src/hardware_validator/detectors/cpu.py`: CPU.
- `src/hardware_validator/detectors/memory.py`: RAM, swap y EDAC.
- `src/hardware_validator/detectors/storage.py`: bloque, particiones y montajes.
- `src/hardware_validator/detectors/gpu.py`: DRM y PCI GPU.
- `src/hardware_validator/detectors/network.py`: interfaces, rutas y DNS locales.
- `src/hardware_validator/report.py`: presentación pura.
- `src/hardware_validator/__main__.py`: CLI.
- `tests/`: pruebas unitarias y fixtures simulados.
- `README.md`: instalación, ejecución, fórmulas y verificación.

## Comandos de uso

```text
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m hardware_validator
```

También puede usarse `.venv/bin/hardware-validator`.
