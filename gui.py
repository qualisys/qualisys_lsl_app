"""
    Simple Tkinter GUI for Qualisys LSL.
"""

import asyncio
import logging
import time
import tkinter as tk
from tkinter import messagebox

import qlsl

LOG_LEVEL = logging.DEBUG
logging.getLogger("qlsl").setLevel(LOG_LEVEL)
LOG = logging.getLogger("gui")
LOG.setLevel(LOG_LEVEL)

class App(tk.Frame):
    def __init__(self, master, async_loop):
        super().__init__(master)
        self.master = master
        self.async_loop = async_loop
        self.master.title("QTM LSL App")
        self.pack()
        self.master.protocol("WM_DELETE_WINDOW", self.close)
        self.create_layout()
        self.set_geometry()
        self.link_handle = None
        self.waiting_for_link = False
        self.start_time = 0
    
    def set_geometry(self):
        ws = self.master.winfo_screenwidth()
        hs = self.master.winfo_screenheight()
        x = int(ws/2.5)
        y = int(hs/2.5)
        self.master.geometry("+{}+{}".format(x, y))
        self.master.update()
        w = self.master.winfo_width()
        h = self.master.winfo_height()
        self.master.minsize(w, h)

    def create_layout(self):
        self.qtm_host = tk.StringVar()
        self.qtm_host.set("127.0.0.1")
        tk.Label(self, text="QTM Server Address    ").grid(row=0, sticky="w")
        self.entry_host = tk.Entry(self, textvariable=self.qtm_host)
        self.entry_host.grid(row=0, column=1)

        self.qtm_port = tk.StringVar()
        self.qtm_port.set(qlsl.QTM_DEFAULT_PORT)
        tk.Label(self, text="QTM Server Port").grid(row=1, sticky="w")
        self.entry_port = tk.Entry(self, textvariable=self.qtm_port)
        self.entry_port.grid(row=1, column=1)

        self.btn_link = tk.Button(
            self, text="Link", width=10, command=self.start_or_stop
        )
        self.btn_link.grid(row=2, column=1, sticky="e")
        self.lbl_time = tk.Label(self, text="")
        self.lbl_time.grid(row=2,  sticky="w")

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
    
    def enable_input(self, enable):
        if enable:
            self.entry_host["state"] = "normal"
            self.entry_port["state"] = "normal"
            self.btn_link["text"] = "Start"
        else:
            self.entry_host["state"] = "disabled"
            self.entry_port["state"] = "disabled"
            self.btn_link["text"] = "Stop"
    
    def on_state_changed(self, new_state):
        if new_state == qlsl.State.INITIAL:
            self.lbl_status["text"] = ""
            self.lbl_time["text"] = ""
            self.lbl_packets["text"] = ""
        elif new_state == qlsl.State.WAITING:
            self.lbl_status["text"] = "Waiting"
        elif new_state == qlsl.State.STREAMING:
            self.lbl_status["text"] = "Streaming"
        elif new_state == qlsl.State.STOPPING:
            self.lbl_status["text"] = "Stopping"
        elif new_state == qlsl.State.STOPPED:
            self.lbl_status["text"] = "Stopped"
            self.enable_input(True)
            self.link_handle = None
            self.waiting_for_link = False
    
    def on_error(self, msg):
        messagebox.showerror("Error", msg)

    def start_or_stop(self):
        if self.waiting_for_link: return
        self.waiting_for_link = True
        if self.link_handle:
            self.do_stop()
        else:
            self.do_start()

    def do_stop(self):
        asyncio.ensure_future(self.link_handle.shutdown())
    
    def do_start(self):
        port_str = self.qtm_port.get()
        try:
            port = int(port_str)
            if port < 0 or port > 65535:
                raise ValueError
        except ValueError:
            self.on_error("'{}' is not a valid port number".format(port_str))
            return
        host = self.qtm_host.get()
        asyncio.ensure_future(self.do_async_start(host, port))

    async def do_async_start(self, host, port):
        try:
            self.link_handle = await qlsl.init_link(
                qtm_host=host,
                qtm_port=port,
                qtm_version=qlsl.QTM_DEFAULT_VERSION,
                on_state_changed=self.on_state_changed,
                on_error=self.on_error,
            )
            await self.link_handle.poll_qtm_state()
            self.enable_input(False)
            self.start_time = time.time()
        except qlsl.LinkError as err:
            self.link_handle = None
            self.on_error(err)
        finally:
            self.waiting_for_link = False

    async def updater(self, interval=1/20):
        LOG.debug("updater enter")
        try:
            while True:
                if self.link_handle:
                    elapsed_time = time.time() - self.start_time
                    self.lbl_time["text"] = "Elapsed time: {}".format(
                        time.strftime('%H:%M:%S', time.gmtime(elapsed_time))
                    )
                    self.lbl_packets["text"] = "Packet count: {}".format(
                        self.link_handle.packet_count,
                    )
                self.update()
                await asyncio.sleep(interval)
        finally:
            LOG.debug("updater exit")
    
    async def stop_async_loop(self):
        self.async_loop.stop()
        LOG.debug("stop_async_loop")
    
    def run_async_loop(self):
        asyncio.ensure_future(self.updater())
        self.async_loop.run_forever()

    def close(self):
        tasks = asyncio.Task.all_tasks()
        for task in tasks:
            task.cancel()
        asyncio.ensure_future(self.stop_async_loop())
        self.master.destroy()

def main():
    root = tk.Tk()
    loop = asyncio.get_event_loop()
    app = App(master=root, async_loop=loop)
    app.run_async_loop()
    loop.close()

if __name__ == "__main__":
    main()
