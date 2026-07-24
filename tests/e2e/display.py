"""Console presentation: durable logging plus a persistent per-instance status
board, so a multi-scenario run's terminal output stays legible instead of
scrolling hundreds of Ansible task lines past whatever actually matters."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.table import Table
from rich.text import Text

from .models import InstanceInfo, Status

_STATUS_STYLES: dict[Status, str] = {
    Status.PASS: "bold green",
    Status.FAIL: "bold red",
    Status.UNREACHABLE: "bold red",
    Status.CONFIG_MISSING: "bold red",
    Status.TUNNEL_FAILED: "bold red",
    Status.TEST_ERROR: "bold red",
}


def setup_logging(run_dir: Path) -> Console:
    """Configures the root logger with a RichHandler for the console (sharing one
    Console with StatusBoard, so log lines scroll cleanly above the persistent
    table instead of fighting it for the terminal) and a FileHandler writing
    everything to run_dir/run.log. The previous version only ever logged to the
    console - a closed terminal or an unattended run meant losing the history."""
    console = Console()
    run_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    rich_handler = RichHandler(console=console, show_path=False, markup=False)
    rich_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(rich_handler)

    file_handler = logging.FileHandler(run_dir / "run.log")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S")
    )
    root.addHandler(file_handler)

    return console


class StatusBoard:
    """A Live-updating table of every instance in the current scenario: what
    phase it's in, what it's doing within that phase, how long it's been there,
    and its final result once known - so it's always visible what the run is
    actually waiting on, without reading Ansible output at all.

    Rebuilds the table from live InstanceInfo state on a timer, from a dedicated
    thread, rather than relying on Rich's own auto-refresh: a Table is a static
    snapshot, so redrawing the *same* Table object on a timer would just repaint
    stale data - the underlying InstanceInfo fields are mutated by worker threads
    (see provisioning.py, verification.py) between refreshes."""

    REFRESH_INTERVAL = 0.5

    def __init__(self, console: Console):
        self._live = Live(console=console, auto_refresh=False, transient=False)
        self._lock = threading.Lock()
        self._scenario = ""
        self._instances: list[InstanceInfo] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start_scenario(self, scenario: str, instances: list[InstanceInfo]) -> None:
        with self._lock:
            self._scenario = scenario
            self._instances = instances

    def _render(self) -> Table:
        with self._lock:
            scenario, instances = self._scenario, list(self._instances)

        # expand=True stretches the table to the full terminal width. Detail (the current
        # Ansible task/curl check) is by far the most variable-length field - giving it the sole
        # `ratio` means it absorbs all the leftover width instead of being sized to its own
        # content, which is what caused the whole table (and every column after it) to jitter
        # on each refresh as task names of different lengths came and went. The other columns
        # come first so their positions stay fixed regardless of what Detail is doing.
        title = Text(f"Scenario: {scenario}") if scenario else None
        table = Table(title=title, expand=True)
        table.add_column("Instance", no_wrap=True)
        table.add_column("Phase", no_wrap=True)
        table.add_column("Elapsed", justify="right", no_wrap=True)
        table.add_column("Result", no_wrap=True)
        table.add_column("Detail", ratio=1, no_wrap=True, overflow="ellipsis")

        for inst in instances:
            style = _STATUS_STYLES.get(inst.status)
            # Result is the one cell that's deliberately Rich markup (a style we control).
            # Everything else is free text - a detected OS name or an Ansible task name could
            # easily contain "[...]"-looking substrings, which Rich would otherwise try to parse
            # as markup instead of displaying literally. Text() renders plain, no parsing.
            result = Text(str(inst.status), style=style) if style else Text(str(inst.status))
            table.add_row(
                Text(inst.display_name),
                Text(str(inst.phase)),
                Text(f"{inst.phase_elapsed:.0f}s"),
                result,
                Text(inst.phase_detail),
            )
        return table

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._live.update(self._render(), refresh=True)
            self._stop_event.wait(self.REFRESH_INTERVAL)

    def __enter__(self) -> StatusBoard:
        self._live.start()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._live.update(self._render(), refresh=True)
        self._live.stop()
