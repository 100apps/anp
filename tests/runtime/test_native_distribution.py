"""原生包的关键安全边界；实际业务验证另见 make native-check。"""
import importlib.util
import json
from pathlib import Path
import tarfile
import io
import signal
import socket
import subprocess
import sys
import struct
import shutil
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def native_runtime_dir():
    import tempfile
    parent = ROOT / "var/native/acceptance"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cli-test-", dir=parent) as directory:
        yield Path(directory)


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_native_artifact_and_source_lock():
    lock = json.loads((ROOT / "native/locks/macos-arm64.json").read_text())
    assert lock["target"] == "macos-arm64"
    for item in lock["artifacts"].values():
        assert item["url"].startswith(("https://github.com/", "https://releases.astral.sh/", "https://nodejs.org/", "https://packages.redis.io/"))
        assert len(item["sha256"]) == 64
    assert lock["artifacts"]["redis"]["url"].startswith("https://packages.redis.io/")


@pytest.mark.parametrize("target", ["macos-arm64", "linux-amd64"])
def test_otel_installs_prometheus_runtime_and_license_without_unused_promtool(tmp_path, monkeypatch, target):
    installer = module("native_otel_runtime_payload", "native/otel_build.py")
    monkeypatch.setattr(installer, "ROOT", tmp_path)
    source = tmp_path / "official/prometheus-locked"
    source.mkdir(parents=True)
    for name, data in (("prometheus", b"official server bytes"), ("promtool", b"optional diagnostics"), ("LICENSE", b"official license")):
        (source / name).write_bytes(data)
    (source / "prometheus").chmod(0o755)
    archive = tmp_path / "official.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=source.name)
    lock = tmp_path / "native/locks" / ("otel-" + target + ".json")
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"artifacts": {"prometheus": {"version": "locked"}}, "licenses": {}}))
    (tmp_path / "native/defaults/otel").mkdir(parents=True)
    bundle = tmp_path / "bundle"
    (bundle / "manifest").mkdir(parents=True)
    def unpack(path, destination):
        with tarfile.open(path) as tar:
            tar.extractall(destination, filter="data")
    monkeypatch.setitem(sys.modules, "platform_build", SimpleNamespace(unpack=unpack, artifact_binary=None))
    installer.install(bundle, target, lambda item: archive, shutil.copytree)
    installed = bundle / "platform/otel/prometheus"
    assert (installed / "prometheus").read_bytes() == b"official server bytes"
    assert (installed / "prometheus").stat().st_mode & 0o111
    assert (installed / "LICENSE").read_bytes() == b"official license"
    assert not (installed / "promtool").exists()


def test_elf_interpreter_relocation_preserves_load_segments_and_existing_bytes(tmp_path):
    runtime = module("native_elf_exec", "native/runtime/elf_exec.py")
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", header, 18, 62)
    struct.pack_into("<Q", header, 32, 64)
    struct.pack_into("<HH", header, 54, 56, 2)
    load = struct.pack("<IIQQQQQQ", 1, 5, 0, 0x400000, 0x400000, 256, 256, 4096)
    interp = struct.pack("<IIQQQQQQ", 3, 4, 176, 0x4000B0, 0x4000B0, 28, 28, 1)
    original = bytes(header) + load + interp + b"/lib64/ld-linux-x86-64.so.2\0" + b"immutable machine code"
    path = tmp_path / "program"
    path.write_bytes(original)
    loader = tmp_path / "搬移 后的目录/tools/libc/ld-linux-x86-64.so.2"
    runtime.set_interpreter(path, loader)
    result = path.read_bytes()
    assert result[:120] == original[:120]  # ELF 头与 PT_LOAD 完全不变。
    assert result[176:len(original)] == original[176:]
    entry = struct.unpack_from("<IIQQQQQQ", result, 120)
    assert entry[2] == len(original)
    assert result[entry[2]:entry[2] + entry[5]] == str(loader).encode() + b"\0"


def test_elf_interpreter_refuses_foreign_architecture(tmp_path):
    runtime = module("native_elf_foreign", "native/runtime/elf_exec.py")
    path = tmp_path / "foreign"
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", header, 18, 183)
    path.write_bytes(header)
    with pytest.raises(RuntimeError, match="x86_64"):
        runtime.set_interpreter(path, tmp_path / "loader")
    assert path.read_bytes() == header


def test_slim_node_preload_uses_rpath_without_splitting_relocation_spaces(tmp_path, monkeypatch):
    runtime = module("native_elf_space_preload", "native/runtime/elf_exec.py")
    root = tmp_path / "完整 解压 中文目录"
    source = root / "platform/storage-api/node/.node-real.elf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"immutable official node")
    libraries = root / "tools/libc"
    libraries.mkdir(parents=True)
    (libraries / "libanp_nss.so").write_bytes(b"own nss")
    patcher = root / "tools/elf/patchelf"
    patcher.parent.mkdir()
    patcher.write_bytes(b"official static patcher")
    calls, executed = [], {}
    monkeypatch.setattr(runtime, "ROOT", root)
    monkeypatch.setenv("LD_PRELOAD", "/foreign/injection.so")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/host/libraries")
    monkeypatch.setattr(runtime.subprocess, "run", lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(runtime, "set_interpreter", lambda *args: None)
    monkeypatch.setattr(runtime.os, "execve", lambda file, argv, env: executed.update(file=str(file), env=env))
    runtime.execute(source.relative_to(root), ["server.js"])
    assert executed["env"]["LD_PRELOAD"] == "libanp_nss.so"
    assert "LD_LIBRARY_PATH" not in executed["env"]
    assert any("--set-rpath" in args and str(libraries) in args for args in calls)
    assert not any("--add-needed" in args for args in calls)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS 离线沙箱契约")
def test_darwin_process_identity_is_stable_inside_offline_sandbox():
    import os
    runtime = module("native_darwin_identity", "native/runtime/darwin_process.py")
    control = module("native_darwin_identity_control", "native/runtime/control.py")
    expected = runtime.identity(os.getpid())
    assert expected and len(expected.split(" ", 1)[0]) == 23
    script = ("import sys;sys.path.insert(0," + repr(str(ROOT / "native/runtime")) + ");"
              "from darwin_process import identity;print(identity(" + str(os.getpid()) + "))")
    result = subprocess.run(["/usr/bin/sandbox-exec", "-p", control.OFFLINE_PROFILE, sys.executable, "-I", "-B", "-c", script],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    child = subprocess.Popen([sys.executable, "-I", "-c", "pass"])
    child.wait(timeout=10)
    assert runtime.identity(child.pid) == ""


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS 离线沙箱契约")
def test_offline_sandbox_allows_local_plugin_ipc_but_denies_external_tcp(native_runtime_dir):
    control = module("native_local_ipc_profile", "native/runtime/control.py")
    endpoint = native_runtime_dir / "ipc.sock"
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(endpoint))
        server.listen(1)
        script = ("import socket,errno; s=socket.socket(socket.AF_UNIX); s.connect(" + repr(str(endpoint)) + "); s.sendall(b'local'); s.close(); "
                  "remote=socket.socket(); remote.settimeout(1); assert remote.connect_ex(('192.0.2.1',9))==errno.EPERM")
        result = subprocess.run(["/usr/bin/sandbox-exec", "-p", control.OFFLINE_PROFILE, sys.executable, "-I", "-B", "-c", script],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        server.settimeout(2)
        client, _ = server.accept()
        with client:
            assert client.recv(5) == b"local"


def test_business_configuration_survives_pid_record_write(tmp_path, monkeypatch):
    platforms = module("native_platform_settings", "native/runtime/platforms.py")
    monkeypatch.setitem(sys.modules, "business_init", SimpleNamespace(ensure_admin=lambda *args: None))
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    controller = SimpleNamespace(ROOT=tmp_path, VAR=tmp_path / "var", env=lambda: {}, write=write)
    config = json.loads((ROOT / "native/defaults/platform.json").read_text())
    config["ports"]["business"] = 20364
    _, environment = platforms.command(controller, "business", config, {"anon-key": "public-test-key"})
    state = controller.VAR / "run/business.json"
    write(state, json.dumps({"pid": 123, "identity": "test", "root": str(tmp_path)}))
    configuration = Path(environment["ANP_BUSINESS_CONFIG"])
    assert configuration != state
    assert json.loads(configuration.read_text())["server"]["port"] == 20364


def test_extract_rejects_parent_escape(tmp_path):
    builder = module("native_builder", "native/build.py")
    archive = tmp_path / "escape.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../../escape")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(tarfile.FilterError):
        builder.extract(archive, tmp_path / "out")
    assert not (tmp_path / "escape").exists()


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("clang"), reason="需要 macOS 签名工具和编译器")
def test_macho_audit_replaces_valid_linker_stub_before_relocation(native_runtime_dir):
    builder = module("native_macho_builder", "native/build.py")
    source = native_runtime_dir / "hello.c"
    source.write_text('#include <stdio.h>\nint main(void) { puts("anp-signature-ok"); return 0; }\n')
    bundle = native_runtime_dir / "bundle"
    bundle.mkdir()
    binary = bundle / "hello"
    subprocess.run(["clang", str(source), "-o", str(binary)], check=True, capture_output=True)
    subprocess.run(["codesign", "--verify", "--strict", str(binary)], check=True, capture_output=True)
    assert "linker-signed" in subprocess.run(["codesign", "-dvv", str(binary)], capture_output=True, text=True).stderr
    receipt = builder.macho_audit(bundle, source_built={"hello"})
    assert receipt["linker_stub_signatures"] == ["hello"]
    assert "linker-signed" not in subprocess.run(["codesign", "-dvv", str(binary)], capture_output=True, text=True).stderr
    moved = native_runtime_dir / "重新 解压"
    shutil.copytree(bundle, moved)
    assert subprocess.check_output([str(moved / "hello")], text=True).strip() == "anp-signature-ok"


def test_pid_reuse_never_signals_another_process(tmp_path, monkeypatch):
    control = module("native_control", "native/runtime/control.py")
    monkeypatch.setattr(control, "VAR", tmp_path)
    (tmp_path / "run").mkdir()
    (tmp_path / "run/redis.json").write_text(json.dumps({"pid": 123, "identity": "old"}))
    monkeypatch.setattr(control, "identity", lambda pid: "different")
    with pytest.raises(RuntimeError, match="PID"):
        control.owned("redis")


def test_postgrest_selects_its_database_dependency():
    control = module("native_control_selection", "native/runtime/control.py")
    assert control.selected({"services": ["postgrest"]}) == ["postgres", "postgrest"]
    with pytest.raises(RuntimeError):
        control.selected({"services": ["realtime"]})


def test_next_title_requires_own_executable_and_working_directory(tmp_path, monkeypatch):
    control = module("native_next_identity", "native/runtime/control.py")
    # 此 fixture 模拟 lsof；Linux 实现必须单独按 /proc 检查，不能误走宿主分支。
    monkeypatch.setattr(control.sys, "platform", "darwin")
    monkeypatch.setattr(control, "ROOT", tmp_path)
    command = "Thu Oct 22 12:18:17 2026     next-server (v16.3.8)"
    node = tmp_path / "tools/node/bin/node"
    cwd = tmp_path / "platform/langfuse/web/web"
    for executable, directory, expected in ((node, cwd, True), (Path('/tmp/other-node'), cwd, False), (node, Path('/tmp/other-project'), False)):
        output = f"fcwd\nn{directory}\nftxt\nn{executable}\n"
        monkeypatch.setattr(control.subprocess, "run", lambda *a, value=output, **kw: SimpleNamespace(stdout=value))
        assert control.retitled_node_owned("langfuse-web", 123, command) is expected
    assert not control.retitled_node_owned("redis", 123, command)


def test_linux_next_title_requires_proc_executable_and_working_directory(tmp_path, monkeypatch):
    linux = module("native_linux_next_identity", "native/runtime/linux_process.py")
    proc = tmp_path / "proc"
    folder = proc / "123"
    folder.mkdir(parents=True)
    original_path = Path
    monkeypatch.setattr(linux, "Path", lambda value: proc if value == "/proc" else original_path(value))
    root = tmp_path / "bundle"
    node = root / "tools/node/bin/node"
    cached_node = root / "var/cache/elf/tools/node/bin/node.elf"
    cwd = root / "platform/langfuse/web/web"
    for executable, directory, expected in ((node, cwd, True), (cached_node, cwd, True),
            (tmp_path / "foreign-node", cwd, False), (node, tmp_path / "foreign-project", False)):
        for name, target in (("exe", executable), ("cwd", directory)):
            link = folder / name
            link.unlink(missing_ok=True)
            link.symlink_to(target)
        assert linux.node_owned(root, 123) is expected


def test_dist_never_contains_runtime_secrets(tmp_path, monkeypatch):
    builder = module("native_dist", "native/build.py")
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "BASE", tmp_path / "var/native")
    bundle = tmp_path / "var/native/build/anp-macos-arm64"
    for folder in ("tools", "storage", "platform", "runtime", "config", "defaults", "manifest", "var/secrets"):
        (bundle / folder).mkdir(parents=True, exist_ok=True)
    (bundle / "manifest/build.json").write_text('{}')
    hashes = {"manifest/build.json": builder.digest(bundle / "manifest/build.json")}
    (bundle / "manifest/files.sha256.json").write_text(json.dumps(hashes))
    (bundle / "var/secrets/fixture.json").write_text('test-only-private-value')
    (bundle / "config/user.json").write_text('test-only-user-config')
    builder.dist("macos-arm64")
    archive = tmp_path / "var/exports/anp-0.1.0-poc-macos-arm64.tar.gz"
    with tarfile.open(archive) as tar:
        assert not any('/var/' in name or name.endswith('user.json') for name in tar.getnames())


def test_dist_deduplicates_equal_content_and_preserves_executable_mode(tmp_path, monkeypatch):
    builder = module("native_dist_hardlinks", "native/build.py")
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "BASE", tmp_path / "var/native")
    bundle = builder.BASE / "build/anp-macos-arm64"
    for folder in ("tools", "storage", "platform", "runtime", "config", "defaults", "manifest", "business"):
        (bundle / folder).mkdir(parents=True)
    (bundle / "manifest/build.json").write_text('{}')
    for name, mode in (("platform/first", 0o644), ("platform/second", 0o644), ("platform/executable", 0o755), ("business/editable", 0o644)):
        (bundle / name).write_bytes(b"same official artifact bytes")
        (bundle / name).chmod(mode)
    hashes = {str(p.relative_to(bundle)): builder.digest(p) for p in bundle.rglob("*") if p.is_file()}
    (bundle / "manifest/files.sha256.json").write_text(json.dumps(hashes))
    builder.dist("macos-arm64")
    archive = tmp_path / "var/exports/anp-0.1.0-poc-macos-arm64.tar.gz"
    with tarfile.open(archive) as tar:
        assert tar.getmember("anp-macos-arm64/platform/second").islnk()
        assert not tar.getmember("anp-macos-arm64/platform/executable").islnk()
        assert not tar.getmember("anp-macos-arm64/business/editable").islnk()
        tar.extractall(tmp_path / "unpacked", filter="data")
    unpacked = tmp_path / "unpacked/anp-macos-arm64"
    for name, expected in hashes.items():
        assert builder.digest(unpacked / name) == expected
    assert (unpacked / "platform/executable").stat().st_mode & 0o777 == 0o755
    (unpacked / "business/editable").write_bytes(b"business development")
    assert (unpacked / "platform/first").read_bytes() == b"same official artifact bytes"


@pytest.mark.parametrize("name,expected,group", [("postgres", signal.SIGINT, False), ("redis", signal.SIGTERM, True)])
def test_stop_signal_uses_postgres_fast_shutdown(tmp_path, monkeypatch, name, expected, group):
    control = module("native_fast_shutdown", "native/runtime/control.py")
    signals = []
    monkeypatch.setattr(control, "owned", lambda service: {"pid": 123})
    monkeypatch.setattr(control, "identity", lambda pid: "")
    monkeypatch.setattr(control, "state_path", lambda service: tmp_path / (service + ".json"))
    monkeypatch.setattr(control.os, "kill", lambda pid, signum: signals.append((pid, signum, False)))
    monkeypatch.setattr(control.os, "killpg", lambda pid, signum: signals.append((pid, signum, True)))
    control.stop_one(name)
    assert signals == [(123, expected, group)]


def test_postgres_shutdown_timeout_keeps_process_record_and_never_sigkills(tmp_path, monkeypatch):
    control = module("native_checkpoint_shutdown", "native/runtime/control.py")
    record = tmp_path / "postgres.json"
    record.write_text('{}')
    signals = []
    monkeypatch.setattr(control, "owned", lambda service: {"pid": 123})
    monkeypatch.setattr(control, "identity", lambda pid: "same live postgres")
    monkeypatch.setattr(control, "state_path", lambda service: record)
    monkeypatch.setattr(control.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(control.os, "kill", lambda pid, signum: signals.append(signum))
    monkeypatch.setattr(control.os, "killpg", lambda pid, signum: signals.append(signum))
    with pytest.raises(RuntimeError, match="检查点"):
        control.stop_one("postgres")
    assert signals == [signal.SIGINT] and record.exists()


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="需要 POSIX 进程与监听查询")
@pytest.mark.parametrize("answer,yes,stopped", [("n", False, False), ("yes", False, True), (None, True, True)])
def test_stop_foreign_listener_confirmation_and_yes(tmp_path, monkeypatch, answer, yes, stopped):
    control = module("native_stop_conflict", "native/runtime/control.py")
    monkeypatch.setattr(control, "PLATFORM", True)
    # 一个进程同时占主端口和附属端口，只能被停止一次。
    child = subprocess.Popen([sys.executable, "-u", "-c", "import socket,time; a=socket.socket();b=socket.socket();a.bind(('127.0.0.1',0));b.bind(('127.0.0.1',0));a.listen();b.listen();print(a.getsockname()[1],b.getsockname()[1],flush=True);time.sleep(60)"], stdout=subprocess.PIPE, text=True)
    try:
        first, second = map(int, child.stdout.readline().split())
        monkeypatch.setattr(control.platforms, "EXTRA_PORTS", {"aux": "redis"})
        def confirm(prompt):
            assert not yes, "-y 不得询问"
            return answer
        monkeypatch.setattr("builtins.input", confirm)
        signals = []
        kill = control.os.kill
        def tracked_kill(pid, sig):
            signals.append((pid, sig))
            kill(pid, sig)
        monkeypatch.setattr(control.os, "kill", tracked_kill)
        config = {"ports": {"redis": first, "aux": second}}
        if stopped:
            control.stop_conflicts(config, ["redis"], yes)
            child.wait(timeout=5)
            assert signals == [(child.pid, signal.SIGTERM)]
            assert not control.listener_pids(first) and not control.listener_pids(second)
        else:
            with pytest.raises(RuntimeError, match="未停止"):
                control.stop_conflicts(config, ["redis"], yes)
            assert child.poll() is None and not signals
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        child.stdout.close()


def test_stop_conflict_never_kills_reused_pid(monkeypatch):
    control = module("native_stop_reused_pid", "native/runtime/control.py")
    monkeypatch.setattr(control, "PLATFORM", False)
    monkeypatch.setattr(control, "listener_pids", lambda port: {123456})
    identities = iter(["A" * 24, "B" * 24])
    monkeypatch.setattr(control, "identity", lambda pid: next(identities, "B" * 24))
    monkeypatch.setattr(control.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="fixture"))
    monkeypatch.setattr(control.time, "sleep", lambda _: None)
    monkeypatch.setattr(control.os, "kill", lambda *a: pytest.fail("不得向更换身份的 PID 发送信号"))
    with pytest.raises(RuntimeError, match="端口仍被占用"):
        control.stop_conflicts({"ports": {"redis": 12345}}, ["redis"], True)


def test_stop_conflict_escalates_only_same_listener(monkeypatch):
    control = module("native_stop_escalation", "native/runtime/control.py")
    monkeypatch.setattr(control, "PLATFORM", False)
    listeners = {123456}
    monkeypatch.setattr(control, "listener_pids", lambda port: set(listeners))
    monkeypatch.setattr(control, "identity", lambda pid: "A" * 24)
    monkeypatch.setattr(control.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="fixture"))
    ticks = iter([0, 11])
    monkeypatch.setattr(control.time, "monotonic", lambda: next(ticks))
    signals = []
    def kill(pid, sig):
        signals.append(sig)
        if sig == signal.SIGKILL:
            listeners.clear()
    monkeypatch.setattr(control.os, "kill", kill)
    control.stop_conflicts({"ports": {"redis": 12345}}, ["redis"], True)
    assert signals == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="需要 POSIX 进程与监听查询")
@pytest.mark.parametrize("option,answer,code,stale", [([], "n\n", 1, False), ([], "y\n", 0, False), (["-y"], "", 0, False), (["-y"], "", 0, True)])
def test_stop_cli_real_foreign_listener(native_runtime_dir, option, answer, code, stale):
    import shutil
    tmp_path = native_runtime_dir
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for name in ("control.py", "platforms.py", "linux_process.py", "darwin_process.py", "external.py", "observability.py"):
        shutil.copy2(ROOT / "native/runtime" / name, runtime / name)
    sockets = [socket.socket() for _ in range(4)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        ports = dict(zip(["postgres", "redis", "postgrest", "litellm"], [s.getsockname()[1] for s in sockets]))
        child = subprocess.Popen([sys.executable, "-u", "-c", "import socket,sys,time;s=socket.socket(fileno=int(sys.argv[1]));s.listen();print('ready',flush=True);time.sleep(60)", str(sockets[1].fileno())], pass_fds=[sockets[1].fileno()], stdout=subprocess.PIPE, text=True)
        assert child.stdout.readline().strip() == "ready"
        for sock in sockets:
            sock.close()
        (tmp_path / "defaults").mkdir()
        (tmp_path / "defaults/anp.json").write_text(json.dumps({"bind": "127.0.0.1", "services": list(ports), "ports": ports}))
        if stale:
            (tmp_path / "var/run").mkdir(parents=True)
            (tmp_path / "var/run/redis.json").write_text(json.dumps({"pid": child.pid, "identity": "stale"}))
        result = subprocess.run([sys.executable, runtime / "control.py", "stop", *option], input=answer, capture_output=True, text=True, timeout=30)
        assert result.returncode == code, result.stderr
        assert ("是否停止" in result.stdout) is (not option)
        if code == 0:
            child.wait(timeout=5)
            assert not (tmp_path / "var/run/redis.json").exists()
        else:
            assert child.poll() is None
    finally:
        for sock in sockets:
            sock.close()
        if "child" in locals():
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
            child.stdout.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="真实原生进程验收仅适用 macOS")
@pytest.mark.parametrize("mode", ["normal", "delayed", "failure"])
def test_start_progress_reuse_and_json_with_native_redis(native_runtime_dir, mode):
    """独立目录和随机端口；不启动或停止用户的实际实例。"""
    import shutil
    tmp_path = native_runtime_dir
    fixture = ROOT / "var/native/build/anp-macos-arm64"
    if not fixture.exists():
        fixture = ROOT / "var/native/run/anp-macos-arm64"
    python = fixture / "tools/python/bin/python3"
    redis = fixture / "storage/redis"
    if not python.exists() or not redis.exists():
        pytest.skip("本机原生制品尚未构建")
    (tmp_path / "runtime").mkdir()
    (tmp_path / "tools/python/bin").mkdir(parents=True)
    (tmp_path / "storage").mkdir()
    (tmp_path / "defaults").mkdir()
    for name in ("control.py", "platforms.py", "darwin_process.py", "external.py", "observability.py"):
        shutil.copy2(ROOT / "native/runtime" / name, tmp_path / "runtime" / name)
    shutil.copy2(ROOT / "native/anp", tmp_path / "anp")
    (tmp_path / "anp").chmod(0o755)
    (tmp_path / "tools/python/bin/python3").symlink_to(python)
    if mode == "normal":
        # libproc 按真实可执行文件路径验证本目录身份，不能链接相邻实例的服务。
        shutil.copytree(redis, tmp_path / "storage/redis", symlinks=True)
    else:
        (tmp_path / "storage/redis").mkdir()
        # 官方 Redis 的 TLS 库位于相邻 lib/；包装器副本必须保留该依赖布局。
        if (redis / "lib").is_dir():
            (tmp_path / "storage/redis/lib").symlink_to(redis / "lib", target_is_directory=True)
        shutil.copy2(redis / "redis-server", tmp_path / "storage/redis/redis-server-real")
        (tmp_path / "storage/redis/redis-cli").symlink_to(redis / "redis-cli")
        wrapper = tmp_path / "storage/redis/redis-server"
        wrapper.write_text('#!/bin/sh\nsleep 7\nexec "$(dirname "$0")/redis-server-real" "$@"\n' if mode == "delayed" else '#!/bin/sh\nsleep 1\nexit 42\n')
        wrapper.chmod(0o755)
    held = [socket.socket() for _ in range(4)]
    try:
        for sock in held:
            sock.bind(("127.0.0.1", 0))
        ports = dict(zip(["postgres", "redis", "postgrest", "litellm"], [s.getsockname()[1] for s in held]))
    finally:
        for sock in held:
            sock.close()
    (tmp_path / "defaults/anp.json").write_text(json.dumps({"bind": "127.0.0.1", "services": ["redis"], "ports": ports}))
    def cli(*args):
        return subprocess.run([str(tmp_path / "anp"), *args], capture_output=True, text=True, timeout=60)
    try:
        first = cli("start")
        if mode == "failure":
            assert first.returncode == 1
            assert "退出码 42" in first.stderr and "回滚" in first.stderr
            assert not (tmp_path / "var/run/redis.json").exists()
            return
        assert first.returncode == 0, first.stderr
        for marker in ("启动顺序：redis", "检查主端口", "[01/01] redis", "日志：", "PID", "就绪", "启动完成"):
            assert marker in first.stderr
        if mode == "delayed":
            assert "仍在等待就绪" in first.stderr
        assert "服务" in first.stdout and "查看状态：./anp status" in first.stdout
        pid = json.loads((tmp_path / "var/run/redis.json").read_text())["pid"]
        second = cli("start")
        assert second.returncode == 0 and "已在运行，复用 PID " + str(pid) in second.stderr
        machine = cli("start", "--json")
        assert machine.returncode == 0 and machine.stderr == ""
        assert json.loads(machine.stdout)["redis"] == {"ready": True, "port": ports["redis"], "pid": pid}
        secret = json.loads((tmp_path / "var/secrets/runtime.json").read_text())
        for value in secret.values():
            assert value not in first.stdout + first.stderr + second.stdout + second.stderr + machine.stdout
    finally:
        stopped = cli("stop")
        assert stopped.returncode == 0, stopped.stderr


def test_linux_prunes_optional_node_targets_without_removing_runtime_metadata(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("anp_linux_elf_prune", ROOT / "native/linux_elf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    package = tmp_path / "platform/langfuse/node_modules/profiler"
    removed = ["prebuilds/linux-arm64/addon.node", "prebuilds/linuxmusl-x64/addon.node", "prebuilds/darwin-arm64/addon.node", "prebuilds/linux-x64/addon.musl.node"]
    kept = ["prebuilds/linux-x64/addon.glibc.node", "prebuilds/linuxglibc-x64/addon.node", "prebuilds/library_config/manifest.json", "prebuilds/datadog-js-zstd/metadata.json", "index.js"]
    for name in removed + kept:
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"upstream fixture")
    receipt = module.prune_node_prebuilds(tmp_path)
    assert len(receipt) == len(removed)
    assert all(not (package / name).exists() for name in removed)
    assert all((package / name).exists() for name in kept)


def test_linux_runpath_patch_preserves_layout_and_rejects_ambiguous_strings(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("anp_linux_runpath", ROOT / "native/linux_elf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = "/ClickHouse/ci/tmp/build/contrib/libllvmlibc-cmake"
    replacement = "$ORIGIN/../../tools/libc"
    header, code = bytes(range(64)), bytes(range(255, 127, -1))
    path = tmp_path / "clickhouse"
    original = header + previous.encode() + b"\0" + code
    path.write_bytes(original)
    module.replace_runpath_in_place(path, previous, replacement)
    result = path.read_bytes()
    assert len(result) == len(original) and result[:64] == header and result[-len(code):] == code
    assert replacement.encode() + b"\0" in result
    ambiguous = original + previous.encode() + b"\0"
    path.write_bytes(ambiguous)
    with pytest.raises(RuntimeError):
        module.replace_runpath_in_place(path, previous, replacement)
    assert path.read_bytes() == ambiguous


def test_linux_transitive_rpath_preserves_dynamic_value_and_file_layout(tmp_path):
    import importlib.util
    import struct
    spec = importlib.util.spec_from_file_location("anp_linux_transitive", ROOT / "native/linux_elf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value = bytearray(152)
    value[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", value, 32, 64)
    struct.pack_into("<HH", value, 54, 56, 1)
    struct.pack_into("<I", value, 64, 2)
    struct.pack_into("<Q", value, 72, 120)
    struct.pack_into("<Q", value, 96, 32)
    struct.pack_into("<QQ", value, 120, 29, 987654)
    path = tmp_path / "clickhouse"
    path.write_bytes(value)
    module.make_runpath_transitive(path)
    expected = bytearray(value)
    struct.pack_into("<Q", expected, 120, 15)
    assert path.read_bytes() == expected


@pytest.mark.parametrize("mode,name,own_loader,process,allowed", [
    ("r--p", "ld-linux-x86-64.so.2", True, "clickhouse", True),
    ("r-xp", "ld-linux-x86-64.so.2", True, "clickhouse", False),
    ("rw-p", "ld-linux-x86-64.so.2", True, "clickhouse", False),
    ("r--p", "libm.so.6", True, "clickhouse", False),
    ("r--p", "ld-linux-x86-64.so.2", False, "clickhouse", False),
    ("r--p", "ld-linux-x86-64.so.2", True, "other", False),
])
def test_linux_optional_symbol_mapping_cannot_allow_host_library_code(tmp_path, mode, name, own_loader, process, allowed):
    import importlib.util
    spec = importlib.util.spec_from_file_location("anp_library_maps", ROOT / "native/checks/platform_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "bundle"
    executable = root / ("var/cache/elf/storage/clickhouse/clickhouse.elf" if process == "clickhouse" else "tools/node/node.elf")
    lines = [f"1000-2000 {mode} 0 00:01 1 /usr/lib/{name}"]
    if own_loader:
        lines.append(f"3000-4000 r-xp 0 00:01 2 {root}/tools/libc/ld-linux-x86-64.so.2")
    foreign, metadata = module.inspect_linux_library_mappings(root, str(executable), lines)
    assert bool(metadata) == allowed and bool(foreign) != allowed


def test_linux_native_loader_patch_is_scoped_to_langfuse_and_checks_upstream_shape(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("anp_native_detection", ROOT / "native/linux_elf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "platform/langfuse/worker/node_modules"
    original = "const isMusl = () => {\n  return true\n}\n"
    for name in ("@langfuse/native", "@node-rs/xxhash", "@unrelated/native"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(json.dumps({"name": name}))
        (folder / "index.js").write_text(original)
    changed = module.normalize_langfuse_native_loader(tmp_path)
    assert len(changed) == 2
    assert "glibcVersionRuntime" in (root / "@langfuse/native/index.js").read_text()
    assert (root / "@unrelated/native/index.js").read_text() == original
    (root / "@langfuse/native/index.js").write_text("unexpected upstream loader")
    with pytest.raises(RuntimeError):
        module.normalize_langfuse_native_loader(tmp_path)


@pytest.mark.parametrize("foreign_command", [None, "LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB", "LC_REEXPORT_DYLIB", "LC_LOAD_UPWARD_DYLIB"])
def test_macho_audit_ignores_install_id_and_fat_headers_but_checks_loaded_libraries(tmp_path, monkeypatch, foreign_command):
    builder = module("native_macho_audit", "native/build.py")
    binary = tmp_path / "Prisma 插件.node"
    binary.write_bytes(b"\xcf\xfa\xed\xfe")
    build_path = "/Users/runner/work/prisma-engines/release/libquery_engine.dylib"
    listing = f"{binary} (architecture x86_64):\n\t{build_path} (compatibility version 0.0.0, current version 0.0.0)\n{binary} (architecture arm64):\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1.0.0)\n"
    commands = f"{binary} (architecture arm64):\nLoad command 0\n          cmd LC_ID_DYLIB\n      cmdsize 128\n         name {build_path} (offset 24)\nLoad command 1\n          cmd LC_LOAD_DYLIB\n      cmdsize 56\n         name /usr/lib/libSystem.B.dylib (offset 24)\n"
    if foreign_command:
        commands += f"Load command 2\n          cmd {foreign_command}\n      cmdsize 128\n         name {build_path} (offset 24)\n"
    monkeypatch.setattr(builder.subprocess, "check_output", lambda args, **kwargs: commands if "-l" in args else listing)
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=""))
    if foreign_command:
        with pytest.raises(SystemExit, match="包外动态库"):
            builder.macho_audit(tmp_path)
    else:
        result = builder.macho_audit(tmp_path)
        assert result["mach_o_files"] == 1 and result["external_dependencies"] == []



def test_macos_redis_preserves_vendor_bytes_and_scopes_library_override(tmp_path, monkeypatch):
    import zipfile
    import hashlib
    installer = module("native_redis_vendor", "native/otel_build.py")
    bundle = tmp_path / "bundle with spaces"
    lib = bundle / "storage/postgres/lib"
    lib.mkdir(parents=True)
    (bundle / "manifest").mkdir()
    libraries = {name: ("official " + name).encode() for name in ("libssl.3.dylib", "libcrypto.3.dylib")}
    for name, data in libraries.items():
        (lib / name).write_bytes(data)
    binaries = {name: ("signed vendor " + name).encode() for name in ("redis-server", "redis-cli")}
    archive = tmp_path / "vendor.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for name, data in binaries.items():
            zipped.writestr("bin/" + name, data)
    def reject_mutation(*args, **kwargs):
        raise AssertionError("不得改写或重新签名官方 Redis")
    monkeypatch.setattr(installer.subprocess, "run", reject_mutation)
    installer.install_redis(bundle, archive)
    receipt = json.loads((bundle / "manifest/redis-distribution.json").read_text())
    for name, data in binaries.items():
        path = bundle / "storage/redis/bin" / name
        assert path.read_bytes() == data
        assert receipt["signed_binaries"][str(path.relative_to(bundle))] == hashlib.sha256(data).hexdigest()
        launcher = bundle / "storage/redis" / name
        assert launcher.stat().st_mode & 0o111
        assert receipt["launchers"][str(launcher.relative_to(bundle))] == hashlib.sha256(launcher.read_bytes()).hexdigest()
    assert all((bundle / "storage/redis/lib" / name).read_bytes() == data for name, data in libraries.items())


@pytest.mark.parametrize("trusted_vendor", [True, False])
@pytest.mark.parametrize("tampered_launcher", [True, False])
def test_redis_library_override_requires_vendor_signature_and_intact_launcher(tmp_path, monkeypatch, trusted_vendor, tampered_launcher):
    import hashlib
    builder = module("native_redis_audit", "native/build.py")
    binary = tmp_path / "storage/redis/bin/redis-server"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\xcf\xfa\xed\xfe vendor fixture")
    launcher = tmp_path / "storage/redis/redis-server"
    launcher.write_text("immutable launcher")
    lib = tmp_path / "storage/redis/lib"
    lib.mkdir()
    (lib / "libssl.3.dylib").write_bytes(b"bundled official OpenSSL")
    manifest = tmp_path / "manifest/redis-distribution.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"signed_binaries": {str(binary.relative_to(tmp_path)): hashlib.sha256(binary.read_bytes()).hexdigest()},
        "launchers": {str(launcher.relative_to(tmp_path)): hashlib.sha256(launcher.read_bytes()).hexdigest()}}))
    if tampered_launcher:
        launcher.write_text("different launcher")
    dependency = "/opt/homebrew/opt/openssl@3/lib/libssl.3.dylib"
    monkeypatch.setattr(builder.subprocess, "check_output", lambda *args, **kwargs: "cmd LC_LOAD_DYLIB\n name " + dependency + " (offset 24)\n")
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="TeamIdentifier=" + ("UUK47G4BAZ" if trusted_vendor else "OTHER")))
    if not trusted_vendor or tampered_launcher:
        with pytest.raises(SystemExit, match="官方 Redis"):
            builder.macho_audit(tmp_path)
    else:
        receipt = builder.macho_audit(tmp_path)
        assert receipt["external_dependencies"] == []
        assert receipt["scoped_library_overrides"] == [{"path": str(binary.relative_to(tmp_path)), "library": dependency,
            "provided_by": "storage/redis/lib/libssl.3.dylib", "vendor_team": "UUK47G4BAZ"}]
        assert not receipt["resigned"]


def test_official_binary_selection_ignores_same_named_directory_and_rejects_ambiguity(tmp_path):
    builder = module("native_platform_binary", "native/platform_build.py")
    (tmp_path / "usr/share/clickhouse").mkdir(parents=True)
    executable = tmp_path / "usr/bin/clickhouse"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"\x7fELF fixture")
    completion = tmp_path / "usr/share/bash-completion/completions/clickhouse"
    completion.parent.mkdir(parents=True)
    completion.write_text("# ClickHouse shell completion\n")
    assert builder.artifact_binary(tmp_path, "clickhouse") == executable
    duplicate = tmp_path / "extra/clickhouse"
    duplicate.parent.mkdir()
    duplicate.write_bytes(b"\x7fELF another executable")
    with pytest.raises(RuntimeError, match="唯一"):
        builder.artifact_binary(tmp_path, "clickhouse")



def test_native_build_tool_locks_use_official_registry_and_integrity():
    for lock in (ROOT / "native/packages").glob("*/package-lock.json"):
        for name, item in json.loads(lock.read_text())["packages"].items():
            if item.get("resolved"):
                assert item["resolved"].startswith("https://registry.npmjs.org/"), str(lock) + ": " + name
                assert item.get("integrity", "").startswith("sha512-"), str(lock) + ": " + name
