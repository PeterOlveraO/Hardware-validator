# Informe de integración del inventario

Fecha: 20 de agosto de 2026.

## Resultado

Los detectores de sistema, CPU, memoria, almacenamiento y GPU están conectados a
una única ejecución de terminal mediante un orquestador validado. Un problema
operativo en un componente produce un objeto vacío válido para ese componente y
no impide ejecutar los detectores posteriores. Los errores de programación o de
contrato son fatales, se comunican por stderr y producen código de salida
distinto de cero.

No se añadieron componentes, dependencias, telemetría, reintentos, red ni
formatos de salida nuevos.

## Flujo integrado

La ejecución normal sigue estos pasos:

1. `__main__.py` procesa `--help` y `--version`.
2. Valida Python 3.11+, Linux y `psutil==7.2.1`.
3. `default_detectors()` crea exactamente `SystemDetector`, `CpuDetector`,
   `MemoryDetector`, `StorageDetector` y `GpuDetector`.
4. `InventoryDetectors` valida que cada componente exponga `detect()` como
   método invocable.
5. `collect_inventory()` ejecuta los cinco detectores secuencialmente y conserva
   un `DetectionResult` por componente.
6. `InventoryResults` valida cada wrapper y el tipo exacto de su modelo público.
7. `render_inventory()` recibe el agregado validado y solo lo presenta; no
   vuelve a detectar hardware.
8. La CLI escribe el reporte en stdout. Los errores fatales se escriben en
   stderr.

La ejecución secuencial es deliberada: las consultas son ligeras y locales, y
evita complejidad, sincronización o carga innecesaria.

## Manejo de errores

### Información opcional ausente

Los detectores convierten archivos opcionales ausentes en `None` o tuplas
vacías. Esto no causa un error de proceso. Cuando la ausencia está contemplada
por la fuente puede conservarse el estado interno `complete`.

### Resultado parcial

Permisos denegados, contenido malformado o una interfaz opcional que falla se
registran dentro del componente como `partial` cuando todavía existen datos
útiles. Los mensajes internos no se presentan como salud del hardware.

### Componente no disponible

Como límite de seguridad adicional, si un detector deja escapar `OSError` o
`NotImplementedError`, el colector crea el modelo vacío correcto y marca solo
ese componente como `unavailable`. Los detectores restantes continúan.

Ejemplos de fallos operativos recuperables:

- `PermissionError` al leer un archivo Linux.
- `FileNotFoundError` producido durante una carrera de sysfs.
- API local no implementada para el kernel o arquitectura.

### Error fatal

No se captura indiscriminadamente cualquier excepción como hardware ausente.
Son fatales:

- Detector sin método `detect()` invocable.
- Retorno que no sea `DetectionResult`.
- Modelo del componente equivocado.
- Modelo público que incumple sus invariantes.
- Resultado agregado con wrapper o tipo incorrecto.
- Excepciones inesperadas como `AttributeError`, `AssertionError` o `TypeError`
  que indiquen un defecto de programación.
- Python o sistema operativo no soportado.
- Dependencia ausente o con versión incompatible.
- Imposibilidad de codificar o escribir el reporte final.

La CLI devuelve `1` para errores internos controlados y deja stdout vacío. Los
argumentos inválidos conservan el código `2` estándar de `argparse`.

## Garantía para presentación

Antes de llamar al formateador se comprueba que existen cinco
`DetectionResult` válidos y que contienen respectivamente:

- `SystemInfo`.
- `CpuInfo`.
- `MemoryInfo`.
- `StorageInfo`.
- `GpuInventory`.

Por ello un fallo operativo no entrega `None`, diccionarios ni objetos de tipo
incorrecto a la presentación. El reporte puede mostrar `Unknown` de forma
consistente sin conocer detalles de ejecución del detector.

## Pruebas añadidas

Se añadió `tests/test_integration.py` con ocho escenarios de integración:

- Fábrica predeterminada con los cinco tipos concretos.
- Ejecución exitosa de todos los componentes hasta el reporte.
- Combinación de resultados completos, parciales y permiso denegado.
- Fallo operativo independiente de sistema, CPU, memoria, almacenamiento y GPU.
- Confirmación de que todos los detectores posteriores siguen ejecutándose.
- Error de programación fatal y no ocultado.
- Agregado inválido rechazado antes del formateador.
- CLI con fallo recuperable y CLI con error fatal, comprobando stdout, stderr y
  códigos de salida.

También se ampliaron las pruebas unitarias del colector para validar cableado y
errores inesperados.

Resultado final:

```text
Ran 84 tests
OK
```

## Verificación real

El flujo completo se ejecutó sin root con escritura de bytecode desactivada y la
salida capturada exclusivamente en memoria:

```text
exit=0 sections=True bytes=5744
```

Una segunda ejecución directa del colector produjo:

```text
system=complete cpu=complete memory=complete storage=complete gpu=complete
VmHWM: 21656 kB
VmRSS: 21656 kB
```

No se desconectó la red física del host porque el entorno no proporciona un
namespace de red aislado y seguro. En su lugar se revisó estáticamente todo el
flujo utilizado y las funciones concretas de la dependencia.

## Dependencias y acceso externo

Dependencias de ejecución:

- `psutil==7.2.1`.

APIs de psutil utilizadas:

- `cpu_count()` usa `os.sysconf`, `/proc/cpuinfo`, `/proc/stat` y sysfs.
- `cpu_freq()` usa `/proc/cpuinfo` y cpufreq sysfs.
- `virtual_memory()` y `swap_memory()` leen `/proc` o APIs nativas Linux.
- `disk_partitions()` lee `/proc/filesystems` y la tabla local de montajes.
- `disk_usage()` llama a `os.statvfs()` solo para montajes locales aceptados por
  el detector.

El código de producción no contiene imports ni llamadas a `subprocess`, sockets,
clientes HTTP, shell o utilidades externas. Tampoco contiene operaciones de
escritura, borrado, renombrado o creación de archivos.

El detector de almacenamiento evita consultar uso para NFS, CIFS, SMB, FUSE y
autofs para no activar servicios o automontajes remotos.

## Duplicación eliminada

Existían dos copias idénticas de la especificación. Se conservó
`docs/v0.1.0-specification.md` como fuente única y se eliminó la copia redundante
`src/v0.1.0-specification.md`.

La especificación autoritativa se actualizó con la taxonomía de errores y las
validaciones del orquestador.

## Archivos modificados en esta integración

- `src/hardware_validator/collector.py`.
- `tests/test_collector.py`.
- `tests/test_integration.py`.
- `docs/v0.1.0-specification.md`.
- `IMPLEMENTATION_REPORT.md`.
- `INTEGRATION_REPORT.md`.
- Eliminado: `src/v0.1.0-specification.md`.

## Limitaciones para lanzamiento

- Los estados `partial` y `unavailable` son internos y no se muestran como
  diagnósticos; la terminal utiliza `Unknown`.
- Un error fatal detiene la recolección en el componente defectuoso. Esto es
  intencional para no ocultar defectos de programación.
- El colector no reintenta fuentes ni ejecuta detectores en paralelo.
- No se probó dentro de un namespace con red físicamente deshabilitada; la
  ausencia de rutas de red se confirmó por revisión de código.
- Python puede crear bytecode por comportamiento normal del intérprete. La
  ejecución de verificación usó `PYTHONDONTWRITEBYTECODE=1`; el producto no
  implementa escrituras propias.

## Comandos de verificación

```text
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m pip check
.venv/bin/hardware-validator --help
.venv/bin/hardware-validator --version
.venv/bin/hardware-validator
```
