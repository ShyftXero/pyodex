"""Demo 01 — PuePy minimal UI."""
from puepy import Application, Page, t

class MinimalPage(Page):
    def initial_state(self):
        return {"clicks": 0}

    def clicked(self):
        self.state["clicks"] += 1

    def populate(self):
        with t.div(classes="card p-4 w-64 bg-gray-900 text-white border border-pink-500 rounded-lg"):
            t.label("Demo 01 — PuePy Minimal UI").classes("font-bold mb-4")
            t.label(lambda: f"{self.state['clicks']} click{'s' if self.state['clicks'] != 1 else ''}").classes("badge text-pink-300 mb-2")
            t.label(lambda: f"You clicked {self.state['clicks']} time(s).\nStill Python, still in this tab.").classes("mb-4")
            t.button(lambda: f"Click me ({self.state['clicks']})", on_click=self.clicked).classes("w-full px-4 py-2 bg-gray-800 border border-pink-500 rounded hover:bg-gray-700")

app = Application()
app.add_page(MinimalPage, "/")
app.mount("#app")
