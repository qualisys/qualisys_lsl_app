"""
    Simple Tkinter GUI for Qualisys LSL.
"""

import argparse
import asyncio
import logging
import time
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

import qlsl.link as link

LOG = logging.getLogger("qlsl")

class App(tk.Frame):
    def __init__(self, master: tk.Tk, async_loop: asyncio.AbstractEventLoop):
        super().__init__(master)
        self.master = master
        self.async_loop = async_loop
        self.master.title("QTM LSL App")
        self.set_icon()
        self.pack()
        self.master.protocol("WM_DELETE_WINDOW", self.close)
        self.create_layout()
        self.set_geometry()
        self.link_handle = None
        self.start_task = None

    def set_icon(self) -> None:
        icon_path = Path("images/qtm.ico")
        if icon_path.exists():
            try:
                self.master.iconbitmap(str(icon_path))
            except Exception as ex:
                LOG.debug(f"Failed to set window icon: {repr(ex)}")

    def set_geometry(self) -> None:
        ws = self.master.winfo_screenwidth()
        hs = self.master.winfo_screenheight()
        x = int(ws / 2.5)
        y = int(hs / 2.5)
        self.master.geometry(f"+{x}+{y}")
        self.master.update()
        w = self.master.winfo_width()
        h = self.master.winfo_height()
        self.master.minsize(w, h)

    def create_layout(self) -> None:
        self.qtm_host = tk.StringVar(value="127.0.0.1")
        tk.Label(self, text="QTM Server Address").grid(row=0, sticky="w")
        self.entry_host = tk.Entry(self, textvariable=self.qtm_host)
        self.entry_host.grid(row=0, column=1)

        self.qtm_port = tk.StringVar(value=link.QTM_DEFAULT_PORT)
        tk.Label(self, text="QTM Server Port").grid(row=1, sticky="w")
        self.entry_port = tk.Entry(self, textvariable=self.qtm_port)
        self.entry_port.grid(row=1, column=1)

        self.btn_link = tk.Button(
            self, text="Start", width=10, command=self.start_or_stop
        )
        self.btn_link.grid(row=2, column=1, sticky="e")

        self.lbl_time = tk.Label(self, text="")
        self.lbl_time.grid(row=2, sticky="w")

        self.lbl_packets = tk.Label(self, text="")
        self.lbl_packets.grid(row=3, sticky="w")
        self.lbl_status = tk.Label(self, text="")
        self.lbl_status.grid(row=3, column=1, sticky="e")

        self.enable_input(True)

        self.grid(padx=25, pady=(15, 20))
        col_count, _ = self.grid_size()
        for col in range(col_count):
            self.grid_columnconfigure(col, pad=5)
        self.grid_rowconfigure(1, pad=10)

    def enable_input(self, enable: bool) -> None:
        state = "normal" if enable else "disabled"
        self.entry_host["state"] = state
        self.entry_port["state"] = state
        self.btn_link["text"] = "Start" if enable else "Stop"

    def on_state_changed(self, new_state: link.State) -> None:
        if new_state == link.State.INITIAL:
            self.lbl_status["text"] = ""
            self.lbl_time["text"] = ""
            self.lbl_packets["text"] = ""
        elif new_state == link.State.WAITING:
            self.lbl_status["text"] = "Waiting on QTM"
        elif new_state == link.State.STREAMING:
            self.lbl_status["text"] = "Streaming"
        elif new_state == link.State.STOPPED:
            self.lbl_status["text"] = "Stopped"
            self.enable_input(True)
            self.link_handle = None

    def on_error(self, msg: str) -> None:
        messagebox.showerror("Error", msg)

    def start_or_stop(self) -> None:
        if self.link_handle:
            self.do_stop()
        else:
            if self.start_task and not self.start_task.done():
                self.start_task.cancel()
            else:
                self.do_start()

    def do_stop(self) -> None:
        asyncio.create_task(self.link_handle.shutdown())

    def do_start(self) -> None:
        port_str = self.qtm_port.get()
        try:
            port = int(port_str)
            if port < 0 or port > 65535:
                raise ValueError
        except ValueError:
            self.on_error(f"'{port_str}' is not a valid port number")
            return

        host = self.qtm_host.get()
        self.start_task = asyncio.create_task(self.do_async_start(host, port))

    async def do_async_start(self, host: str, port: int) -> None:
        try:
            self.lbl_status["text"] = "Connecting to QTM"
            self.enable_input(False)
            self.link_handle = await link.init(
                qtm_host=host,
                qtm_port=port,
                qtm_version=link.QTM_DEFAULT_VERSION,
                on_state_changed=self.on_state_changed,
                on_error=self.on_error,
            )
        except asyncio.CancelledError:
            self.link_handle = None
            LOG.error("Start attempt canceled")
        except link.LinkError as err:
            self.link_handle = None
            self.on_error(str(err))
        except Exception as ex:
            self.link_handle = None
            LOG.error("gui: do_async_start exception: %r", ex)
            self.on_error("An internal error occurred. See log messages for details.")
        finally:
            if not self.link_handle:
                self.enable_input(True)
                self.lbl_status["text"] = "Start failed"
            self.start_task = None

    def format_packet_count(self, count: int) -> str:
        if count > 1_000_000:
            return f"{count // 1_000_000}.{(count % 1_000_000) // 10_000:02d}m"
        if count > 1_000:
            return f"{count // 1_000}.{(count % 1_000) // 10:02d}k"
        return str(count)

    def format_time(self, tm: float) -> str:
        return time.strftime('%H:%M:%S', time.gmtime(tm))

    def display_link_info(self) -> None:
        if self.link_handle and self.link_handle.is_streaming():
            elapsed_time = self.link_handle.elapsed_time()
            self.lbl_time["text"] = f"Elapsed time: {self.format_time(elapsed_time)}"
            packet_count = self.link_handle.packet_count
            self.lbl_packets["text"] = f"Packet count: {self.format_packet_count(packet_count)}"

    async def updater(self, interval: float = 1 / 20) -> None:
        try:
            LOG.debug("gui: updater enter")
            while True:
                self.display_link_info()
                self.update()
                await asyncio.sleep(interval)
        finally:
            LOG.debug("gui: updater exit")

    async def stop_async_loop(self) -> None:
        self.async_loop.stop()
        self.master.destroy()
        LOG.debug("gui: stop_async_loop")

    def close(self) -> None:
        for task in asyncio.all_tasks():
            task.cancel()
        asyncio.create_task(self.stop_async_loop())


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Qualisys LSL App.")
    parser.add_argument(
        "-v", "--verbose",
        dest="verbose",
        action="store_true",
        default=False,
        help="log debug messages"
    )
    args = parser.parse_args()

    # Configure logging based on the verbosity flag
    configure_logging(args.verbose)

    root = tk.Tk()
    app = App(master=root, async_loop=asyncio.get_running_loop())
    await app.updater()

if __name__ == "__main__":
    asyncio.run(main())
