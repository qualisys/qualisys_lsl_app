"""
    Simple Tkinter GUI for Qualisys LSL.
"""

import asyncio
import logging
import time
import tkinter as tk
from tkinter import messagebox
from qtm import QRTEvent

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
        self.link_ctx = None
        self.is_linked = False
    
    def set_geometry(self):
        self.master.update()
        ws = self.master.winfo_screenwidth()
        hs = self.master.winfo_screenheight()
        w = self.master.winfo_width()
        h = self.master.winfo_height()
        x = int((ws/2) - (w/2))
        y = int((hs/2) - (h/2))
        self.master.minsize(w, h)
        self.master.geometry("{}x{}+{}+{}".format(w, h, x, y))

    def create_layout(self):
        self.qtm_host = tk.StringVar()
        self.qtm_host.set("127.0.0.1")
        tk.Label(self, text="QTM Server Address").grid(row=0, sticky="w")
        self.entry_host = tk.Entry(self, textvariable=self.qtm_host)
        self.entry_host.grid(row=0, column=1)

        self.qtm_port = tk.StringVar()
        self.qtm_port.set(qlsl.QTM_DEFAULT_PORT)
        tk.Label(self, text="QTM Server Port").grid(row=1, sticky="w")
        self.entry_port = tk.Entry(self, textvariable=self.qtm_port)
        self.entry_port.grid(row=1, column=1)

        self.lbl_status = tk.Label(self, text="")
        self.lbl_status.grid(row=2, sticky="w")

        self.btn_link = tk.Button(
            self, text="Link", width=10, command=self.link_or_unlink
        )
        self.btn_link.grid(row=2, column=1, sticky="e")

        self.lbl_time = tk.Label(self, text="")
        self.lbl_time.grid(row=3, columnspan=2, sticky="w")
        self.lbl_packets = tk.Label(self, text="")
        self.lbl_packets.grid(row=4, columnspan=2, sticky="w")

        self.grid(padx=50, pady=(20, 20))
        col_count, _ = self.grid_size()
        for col in range(col_count):
            self.grid_columnconfigure(col, pad=5)
        self.grid_rowconfigure(1, pad=10)

    def link_or_unlink(self):
        if self.is_linked:
            self.do_unlink()
        else:
            self.do_link()
    
    def set_is_linked(self, is_linked):
        self.is_linked = is_linked
        if self.is_linked:
            self.entry_host["state"] = "disabled"
            self.entry_port["state"] = "disabled"
            self.btn_link["text"] = "Unlink"
            self.lbl_status["text"] = "In progress"
            self.lbl_status["fg"] = "green"
        else:
            self.entry_host["state"] = "normal"
            self.entry_port["state"] = "normal"
            self.btn_link["text"] = "Link"
            self.lbl_status["text"] = "Stopped"
            self.lbl_status["fg"] = "red"
    
    def qtm_on_event(self, event):
        if event == QRTEvent.EventRTfromFileStopped:
            self.do_unlink()
            messagebox.showinfo("Info", "QTM stream stopped")
        LOG.debug("qtm_on_event: {}".format(event))
    
    def qtm_on_disconnect(self, exc):
        if exc:
            LOG.debug('on_disconnect: {}'.format(exc))
        self.set_is_linked(False)
    
    def do_link(self):
        port_str = self.qtm_port.get()
        try:
            port = int(port_str)
            if port < 0 or port > 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Error",
                "'{}' is not a valid port number".format(port_str)
            )
            return
        host = self.qtm_host.get()
        asyncio.ensure_future(self.do_async_link(host, port))

    def do_unlink(self):
        if self.link_ctx:
            asyncio.ensure_future(self.do_async_unlink())
    
    async def do_async_link(self, host, port):
        if self.link_ctx: return
        try:
            self.link_ctx = await qlsl.link(
                host, port, self.qtm_on_event, self.qtm_on_disconnect
            )
        except qlsl.LinkError as err:
            self.link_ctx = None
            messagebox.showerror("Error", err)
        if self.link_ctx:
            self.set_is_linked(True)
            self.start_time = time.time()

    async def do_async_unlink(self):
        await qlsl.unlink(self.link_ctx)
        self.link_ctx = None
    
    async def updater(self, interval=1/20):
        LOG.debug("updater enter")
        try:
            while True:
                if self.link_ctx:
                    elapsed_time = time.time() - self.start_time
                    self.lbl_time["text"] = "Elapsed time: {}".format(
                        time.strftime('%H:%M:%S', time.gmtime(elapsed_time))
                    )
                    self.lbl_packets["text"] = "Packet count: {}".format(
                        self.link_ctx.lsl.packet_count,
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
