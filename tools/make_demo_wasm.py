"""Emit a tiny hand-encoded wasm module for demo 12 (no toolchain required).

Exports: add(i32,i32)->i32, memory, run()->() which calls the imported env.log(42).
WASM is a binary format, not a compiled-language feature — writing it by hand keeps the
demo working on a machine with no emscripten, clang or wat2wasm.
"""

from pathlib import Path


def leb(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def nm(text: str | bytes) -> bytes:
    """Wasm names are length-prefixed bytes, not LEB vectors."""
    raw = text.encode() if isinstance(text, str) else text
    return leb(len(raw)) + raw


def vec(items: list[bytes]) -> bytes:
    return leb(len(items)) + b"".join(items)


def section(sid: int, payload: bytes) -> bytes:
    return bytes([sid]) + leb(len(payload)) + payload


def build() -> bytes:
    i32 = b"\x7f"
    types = vec([
        b"\x60" + vec([i32, i32]) + vec([i32]),   # 0: (i32,i32)->i32
        b"\x60" + vec([i32]) + vec([]),            # 1: (i32)->()
        b"\x60" + vec([]) + vec([]),                # 2: ()->()
    ])
    imports = vec([nm("env") + nm("log") + b"\x00" + leb(1)])  # module, name, kind=func, type=1
    funcs = vec([leb(0), leb(2)])            # defined funcs: 1=add(type 0), 2=run(type 2)
    mem = vec([b"\x00" + leb(1)])                               # min 1 page (64 KiB)
    exports = vec([
        nm("add") + b"\x00" + leb(1),
        nm("memory") + b"\x02" + leb(0),
        nm("run") + b"\x00" + leb(2),
    ])
    add_body = b"\x00" + b"\x20\x00\x20\x01\x6a" + b"\x0b"       # local.get 0,1; i32.add
    run_body = b"\x00" + b"\x41\x2a" + b"\x10\x00" + b"\x0b"     # i32.const 42; call 0
    code = vec([leb(len(add_body)) + add_body, leb(len(run_body)) + run_body])

    return (
        b"\x00asm\x01\x00\x00\x00"
        + section(1, types)
        + section(2, imports)
        + section(3, funcs)
        + section(5, mem)
        + section(7, exports)
        + section(10, code)
    )


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "examples/demos/12-wasm/guest.wasm"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build())
    print(f"wrote {target} ({target.stat().st_size} bytes)")
