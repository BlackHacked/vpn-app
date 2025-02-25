import flet as ft
import ipaddress
import re
import yaml
import pathlib
import platform
import subprocess
from multiprocessing import Process, Queue, freeze_support
import os
import time
import threading
import ctypes
import sys
import signal
from queue import Empty
from pathlib import Path

def resolve_path(path):
    if getattr(sys, "frozen", False):
        resolved_path = Path(sys.executable).parent.joinpath(path)
    else:
        resolved_path = Path(__file__).parent.joinpath(path)
    return resolved_path


app_dir = resolve_path(".")
def is_admin():
    try:
        if os.name == 'nt':
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.geteuid() == 0
    except AttributeError:
        return os.getuid() == 0 if hasattr(os, 'getuid') else False
    except Exception as e:
        print(f"检查权限时出错: {e}")
        return False

def run_subprocess(command: str, config_file_path: str, output_queue: Queue, subprocess_pids: list, blocking: bool = True):
    """Run a subprocess command, optionally blocking, and send output to queue"""
    process = None
    try:
        print(f"Executing command in {config_file_path}: {command}")
        exe_name = 'pgcli_win.exe' if platform.system() == "Windows" else 'pgcli_macos'
        exe_path = os.path.join(config_file_path, exe_name)
        if not os.path.exists(exe_path):
            output_queue.put(f"错误: 未找到 {exe_name}")
            return
            
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=config_file_path
        )
        
        # 立即记录 PID
        subprocess_pids.append(process.pid)
        print(f"Subprocess started with PID: {process.pid}")
        
        def enqueue_output(pipe, queue):
            """将子进程输出放入队列"""
            for line in iter(pipe.readline, ''):
                queue.put(f"[{time.ctime()}] {line.strip()}")
            pipe.close()

        # 为 stdout 和 stderr 创建非阻塞读取线程
        stdout_thread = threading.Thread(target=enqueue_output, args=(process.stdout, output_queue), daemon=True)
        stderr_thread = threading.Thread(target=enqueue_output, args=(process.stderr, output_queue), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        if blocking:
            # 对于阻塞任务（如注册），等待进程完成
            process.wait()
            stdout_thread.join()
            stderr_thread.join()
            output_queue.put(f"进程退出代码: {process.returncode}")
        else:
            # 对于非阻塞任务（如连接），不等待进程结束
            output_queue.put(f"[{time.ctime()}] Subprocess {process.pid} started (non-blocking)")

        return process  # 返回进程对象以便后续管理

    except Exception as ex:
        output_queue.put(f"子进程错误: {ex}")
        return None
    finally:
        if process and process.poll() is None and blocking:
            process.terminate()
            time.sleep(0.1)
            if process.poll() is None:
                process.kill()

def run_register_vpn_cmd(vpn_url: str, vpn_key: str, config_file_path: str, output_queue: Queue, subprocess_pids: list):
    """Run VPN registration command (blocking)"""
    if platform.system() == "Windows":
        command = f'"{os.path.join(config_file_path, "pgcli_win.exe")}" admin secret --secret-key "{vpn_key}" --network "{vpn_url}" --duration 876500h > "{os.path.join(config_file_path, "psns.json")}"'
    else:
        command = f'"{os.path.join(config_file_path, "pgcli_macos")}" admin secret --secret-key "{vpn_key}" --network "{vpn_url}" --duration 876500h > "{os.path.join(config_file_path, "psns.json")}"'
    print(f"Running register command: {command}")
    run_subprocess(command, config_file_path, output_queue, subprocess_pids, blocking=True)

def run_connect_vpn_cmd(vpn_url: str, ip_address: str, config_file_path: str, output_queue: Queue, subprocess_pids: list):
    """Run VPN connection command (non-blocking)"""
    print(f"Running start connect")
    if is_admin():
        print("程序以管理员权限运行")
        if platform.system() == "Windows":
            command = f'"{os.path.join(config_file_path, "pgcli_win.exe")}" vpn -s "{vpn_url}" -4 "{ip_address}/24" --secret-file "{os.path.join(config_file_path, "psns.json")}"'
        else:
            command = f'"{os.path.join(config_file_path, "pgcli_macos")}" vpn -s "{vpn_url}" -4 "{ip_address}/24" --secret-file "{os.path.join(config_file_path, "psns.json")}"'
    else:
        print("程序未以管理员权限运行")
        if platform.system() == "Windows":
            command = f'"{os.path.join(config_file_path, "pgcli_win.exe")}" vpn -s "{vpn_url}" -4 "{ip_address}/24" --secret-file "{os.path.join(config_file_path, "psns.json")}" --proxy-listen 127.0.0.1:8080 --forward tcp://127.0.0.1:80 --forward udp://223.5.5.5:53'
        else:
            command = f'"{os.path.join(config_file_path, "pgcli_macos")}" vpn -s "{vpn_url}" -4 "{ip_address}/24" --secret-file "{os.path.join(config_file_path, "psns.json")}" --proxy-listen 127.0.0.1:8080 --forward tcp://127.0.0.1:80 --forward udp://223.5.5.5:53'
    print(f"Running connect command: {command}")
    return run_subprocess(command, config_file_path, output_queue, subprocess_pids, blocking=False)

def save_app_config(app_config_file_path: str, vpn_key: str, vpn_url: str, ip_address: str):
    """Save configuration to YAML file"""
    try:
        with open(app_config_file_path, 'w') as f:
            data = {"vpn_key": vpn_key, "vpn_url": vpn_url, "ip_address": ip_address}
            yaml.dump(data, f)
    except Exception as ex:
        print(f"Failed to save config: {ex}")

def load_app_config(app_config_file_path: str) -> dict:
    """Load configuration from YAML file"""
    try:
        with open(app_config_file_path, 'r') as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}

def main(page: ft.Page):
    page.title = "MEASURE VPN"
    page.vertical_alignment = ft.MainAxisAlignment.CENTER
    
    # Paths setup
    assets_dir = os.path.join(app_dir, "assets")
    app_config_file_path = os.path.join(app_dir, "data.yaml")
    
    os.makedirs(assets_dir, exist_ok=True)
    # UI Elements
    vpn_key = ft.TextField(label="VPN密钥")
    
    def check_vpn_url(e):
        pattern = r"^(ws|wss)://((?:[a-zA-Z0-9-\.]+|(?:\[\d{1,3}\.){3}\d{1,3}\])|(?:\[\S+\]))(:\d+)?(/.*)?$"
        e.control.error_text = None if bool(re.match(pattern, e.control.value)) else "无效的VPN地址"
        page.update()
    
    vpn_url = ft.TextField(label="VPN地址", on_change=check_vpn_url)
    
    def check_ipaddress(e):
        try:
            ipaddress.ip_address(e.control.value)
            e.control.error_text = None
        except ValueError:
            e.control.error_text = "IP地址无效"
        page.update()
    
    ip_address = ft.TextField(label="VPN内网IP", suffix_text="/24", on_change=check_ipaddress)
    cmd_text = ft.TextField(
        label="控制台",
        multiline=True,
        min_lines=10,
        max_lines=10,
        disabled=True
    )
    
    output_queue = Queue()
    processes = []  # 用于存储 Process 对象
    subprocess_pids = []  # 用于存储 subprocess.Popen 的 PID
    subprocess_objects = []  # 用于存储非阻塞的 subprocess.Popen 对象

    def update_console():
        """Update console output from queue"""
        try:
            while True:
                line = output_queue.get_nowait()
                cmd_text.value = f"{cmd_text.value}\n{line}" if cmd_text.value else line
        except Empty:
            pass
        page.update()

    def button_clicked(e):
        nonlocal processes, subprocess_pids, subprocess_objects
        if not all([vpn_key.value, vpn_url.value, ip_address.value]):
            vpn_url.error_text = "请输入VPN地址" if not vpn_url.value else None
            vpn_key.error_text = "请输入VPN密钥" if not vpn_key.value else None
            ip_address.error_text = "请输入VPN内网IP" if not ip_address.value else None
            page.update()
            return

        try:
            vpn_key.error_text = vpn_url.error_text = ip_address.error_text = None
            save_app_config(app_config_file_path, vpn_key.value, vpn_url.value, ip_address.value)
            
            cmd_text.value = "VPN注册信息中..."
            conn_btn.text = "VPN连接中"
            dis_conn_btn.visible = True
            page.update()

            # Start registration process (blocking)
            reg_process = Process(target=run_register_vpn_cmd, 
                                args=(vpn_url.value, vpn_key.value, assets_dir, output_queue, subprocess_pids))
            reg_process.daemon = True
            try:
                reg_process.start()
                processes.append(reg_process)
                print(f"Registration process started with PID: {reg_process.pid}")
            except Exception as ex:
                cmd_text.value = f"注册进程启动失败: {ex}"
                page.update()
                return

            # 等待注册进程完成
            reg_process.join()
            time.sleep(1)

            # Start connection process (non-blocking)
            conn_process = Process(target=run_connect_vpn_cmd, 
                                 args=(vpn_url.value, ip_address.value, assets_dir, output_queue, subprocess_pids))
            conn_process.daemon = True
            try:
                conn_process.start()
                processes.append(conn_process)
                print(f"Connection process started with PID: {conn_process.pid}")
                # 获取 subprocess.Popen 对象
                conn_subprocess = run_connect_vpn_cmd(vpn_url.value, ip_address.value, assets_dir, output_queue, subprocess_pids)
                if conn_subprocess:
                    subprocess_objects.append(conn_subprocess)
            except Exception as ex:
                cmd_text.value = f"连接进程启动失败: {ex}"
                page.update()
                return

            def periodic_update():
                while any(p.is_alive() for p in processes) or not output_queue.empty():
                    update_console()
                    for p in processes[:]:
                        if not p.is_alive() and p.exitcode is not None:
                            p.join()
                            processes.remove(p)
                    time.sleep(0.5)
                update_console()
                if not processes:
                    cmd_text.value = cmd_text.value + "\n进程已完成"
                    page.update()

            threading.Thread(target=periodic_update, daemon=True).start()

        except Exception as ex:
            cmd_text.value = f"发生错误: {ex}"
            print(f"Error in button_clicked: {ex}")
            page.update()

    def disconnect_vpn(e):
        nonlocal processes, subprocess_pids, subprocess_objects
        # 终止 multiprocessing Process
        for p in processes[:]:
            if p.is_alive():
                try:
                    p.kill()
                    print(f"Terminated multiprocessing process {p.pid}")
                    p.join(timeout=1.0)
                except Exception as ex:
                    print(f"Failed to kill multiprocessing process {p.pid}: {ex}")
                    if platform.system() == "Windows":
                        subprocess.run(f"taskkill /PID {p.pid} /F", shell=True)
                    else:
                        os.kill(p.pid, signal.SIGKILL)
                if p in processes:
                    processes.remove(p)
        
        # 终止 subprocess.Popen 创建的子进程
        for proc in subprocess_objects[:]:
            if proc.poll() is None:
                try:
                    proc.kill()
                    print(f"Terminated subprocess {proc.pid}")
                    proc.wait(timeout=1.0)
                except Exception as ex:
                    print(f"Failed to kill subprocess {proc.pid}: {ex}")
                    if platform.system() == "Windows":
                        subprocess.run(f"taskkill /PID {proc.pid} /F", shell=True)
                    else:
                        os.kill(proc.pid, signal.SIGKILL)
                subprocess_objects.remove(proc)

        for pid in subprocess_pids[:]:
            try:
                if platform.system() == "Windows":
                    subprocess.run(f"taskkill /PID {pid} /F", shell=True)
                    print(f"Terminated subprocess {pid}")
                else:
                    os.kill(pid, signal.SIGKILL)
                    print(f"Terminated subprocess {pid}")
            except Exception as ex:
                print(f"Failed to kill subprocess {pid}: {ex}")
            subprocess_pids.remove(pid)

        processes.clear()
        subprocess_pids.clear()
        subprocess_objects.clear()
        cmd_text.value = "VPN disconnected"
        conn_btn.text = "连接到VPN"
        dis_conn_btn.visible = False
        page.update()

    conn_btn = ft.ElevatedButton(text="连接到VPN", on_click=button_clicked)
    dis_conn_btn = ft.ElevatedButton(text="断开VPN", visible=False, on_click=disconnect_vpn)
    
    page.add(vpn_key, vpn_url, ip_address, conn_btn, dis_conn_btn, cmd_text)
    
    # Load existing config
    if os.path.exists(app_config_file_path):
        data = load_app_config(app_config_file_path)
        vpn_key.value = data.get("vpn_key", "")
        vpn_url.value = data.get("vpn_url", "")
        ip_address.value = data.get("ip_address", "")
        page.update()

if __name__ == '__main__':
    freeze_support()
    print(f"app_dir:{app_dir}")
    ft.app(target=main, assets_dir=f"{app_dir}/assets")