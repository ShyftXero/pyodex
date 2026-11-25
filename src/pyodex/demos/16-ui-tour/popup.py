"""Demo 16 — PuePy component vocabulary."""
from puepy import Application, Page, t
from pyodex import chrome

MENU = ["Search this page", "Reading mode", "Capture tab", "Settings"]


class UiTourPage(Page):
    def initial_state(self):
        return {"active": 0, "query": "", "tabs": "0"}

    async def on_mount(self):
        self.state["tabs"] = f"{await chrome.tabs.count()} tabs"

    def activate(self, index: int):
        self.state["active"] = index

    def typed(self, event):
        self.state["query"] = event.target.value

    def open_item(self):
        print(f"opened: {MENU[self.state['active']]}")

    def dismiss(self):
        pass

    def populate(self):
        with t.div(classes="px-shell w-96 bg-gray-900 text-white border border-gray-700 rounded-xl overflow-hidden"):
            with t.div(classes="px-head flex items-center gap-4 p-4"):
                t.span(classes="px-dot w-2 h-2 rounded-full bg-pink-500")
                t.span("pyodex", classes="px-title font-bold")
                t.span("ui tour", classes="px-pill text-xs font-bold tracking-wider text-pink-300 bg-pink-500/10 border border-pink-500/35 rounded-full px-2 py-1 ml-auto")

            with t.div(classes="px-menu border-t border-gray-700"):
                for i, item in enumerate(MENU):
                    with t.div(
                        classes=lambda i=i: f"px-item flex items-center gap-4 px-4 py-2 cursor-pointer border-b border-gray-800 "
                            f"{'bg-pink-500/10 shadow-[inset_2px_0_0_pink-500]' if self.state['active'] == i else ''} "
                            f"hover:bg-gray-800 transition-colors",
                        attrs={"data-active": lambda i=i: "1" if self.state['active'] == i else "0", "data-index": str(i)},
                        on_click=lambda i=i: self.activate(i),
                    ):
                        t.span(item)
                        t.span(["⌘K", "⌘R", "⌘⇧4", "⌘,"][i], classes="px-k text-xs font-bold text-gray-500 border border-gray-600 rounded px-1 ml-auto")

            with t.div(classes="px-body p-4 gap-4"):
                t.input(
                    placeholder="Filter…",
                    bind="query",
                    classes="px-input w-full px-3 py-2 rounded-lg border border-gray-600 bg-gray-800 text-white focus:outline-none focus:border-pink-500 focus:ring-2 focus:ring-pink-500/20",
                )
                with t.div(classes="px-row flex gap-2"):
                    t.button("Open", on_click=self.open_item, classes="px-btn px-btn--primary flex-1 py-2 rounded-lg border border-pink-500 bg-pink-500 text-black font-bold")
                    t.button("Dismiss", on_click=self.dismiss, classes="px-btn flex-1 py-2 rounded-lg border border-gray-600 bg-gray-800 hover:border-gray-500")

            with t.div(classes="px-foot px-4 py-2 border-t border-gray-700 text-gray-500 text-xs font-mono flex justify-between"):
                t.span(lambda: f"{len(self.state['query'])} chars")
                t.span("python · dom")
                t.span(lambda: self.state["tabs"])


app = Application()
app.add_page(UiTourPage, "/")
app.mount("#app")
