#!/usr/bin/env python3
"""
Docker镜像同步脚本 - GitHub Actions专用版
严格保持路径转换逻辑：
mysql -> docker.cnb.cool/group/mysql
mysql:5.7 -> docker.cnb.cool/group/mysql:5.7
whyour/qinglong -> docker.cnb.cool/group/whyour/qinglong
ghcr.io/tonc/qinglong -> docker.cnb.cool/group/tonc/qinglong
"""

import os
import sys
import json
import requests
import subprocess
import logging
from pathlib import Path

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('docker-sync')

# 从环境变量获取配置
CONFIG = {
    'token': os.getenv('DOCKER_REGISTRY_TOKEN'),
    'group': os.getenv('DOCKER_REGISTRY_GROUP')
}

HEADERS = {
    'accept': 'application/json',
    'Authorization': CONFIG['token'],
    'Content-Type': 'application/json'
}

def ensure_repo_exists(repo):
    """确保目标仓库存在"""
    json_data = {
        'description': 'Auto-created by sync tool',
        'license': 'MIT',
        'name': repo,
        'visibility': 'public'
    }
    try:
        response = requests.post(
            f'https://api.cnb.cool/{CONFIG["group"]}/-/repos',
            headers=HEADERS,
            json=json_data
        )
        if response.status_code == 409:
            logger.info(f"仓库已存在: {repo}")
        else:
            response.raise_for_status()
            logger.info(f"已创建仓库: {repo}")
    except Exception as e:
        logger.error(f"仓库操作失败: {repo} - {str(e)}")
        raise

# def copy_image(src, dest):
#     """使用skopeo复制镜像"""
#     try:
#         cmd = [
#             "skopeo", "copy", "--all",
#             "--retry-times", "3",
#             f'docker://{src}',
#             f'docker://{dest}'
#         ]
#         logger.info(f"复制中: {src} -> {dest}")
#         result = subprocess.run(
#             cmd,
#             check=True,
#             stdout=subprocess.PIPE,
#             stderr=subprocess.PIPE,
#             text=True
#         )
#         logger.debug(f"命令输出:\n{result.stdout}")
#         return True
#     except subprocess.CalledProcessError as e:
#         logger.error(f"复制失败: {src}\n{e.stderr}")
#         return False

def copy_image(src, dest):
    """使用skopeo复制镜像，排除windows平台"""
    try:
        # 获取镜像的所有平台信息
        inspect_cmd = ["skopeo", "inspect", "--raw", f"docker://{src}"]
        inspect_result = subprocess.run(
            inspect_cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False  # 返回二进制数据
        )
        manifest_data = json.loads(inspect_result.stdout)

        # 提取所有平台信息
        platforms = []
        for manifest in manifest_data.get("manifests", []):
            os_platform = manifest.get("platform", {})
            os_name = os_platform.get("os", "")
            architecture = os_platform.get("architecture", "")
            platform_str = f"{os_name}/{architecture}"
            platforms.append(platform_str)

        # 过滤掉windows平台
        filtered_platforms = [p for p in platforms if not p.startswith("windows/")]

        if not filtered_platforms:
            logger.error(f"没有可复制平台（全部被排除）: {src}")
            return False

        # 对每个平台执行复制
        success = True
        for platform in filtered_platforms:
            logger.info(f"复制平台: {platform} - {src} -> {dest}")
            copy_cmd = [
                "skopeo", "copy", "--all",
                "--retry-times", "3",
                "--platform", platform,
                f"docker://{src}",
                f"docker://{dest}"
            ]
            copy_result = subprocess.run(
                copy_cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            logger.debug(f"平台复制输出:\n{copy_result.stdout}")

        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"复制失败: {src}\n{e.stderr}")
        return False
    except json.JSONDecodeError as e:
        logger.error(f"解析镜像清单失败: {src} - {str(e)}")
        return False
    except Exception as e:
        logger.error(f"复制过程中发生未知错误: {src} - {str(e)}")
        return False

def process_image_line(line):
    """处理单行镜像定义"""
    line = line.strip()
    if not line:
        return None

    parts = line.split('/')
    
    # 确定目标路径
    if len(parts) == 1:  # mysql 或 mysql:5.7
        repo = parts[0].split(':')[0]
        dest = f'docker.cnb.cool/{CONFIG["group"]}/{parts[0]}'
    elif len(parts) == 2:  # whyour/qinglong
        repo = parts[0]
        dest = f'docker.cnb.cool/{CONFIG["group"]}/{parts[0]}/{parts[1]}'
    elif len(parts) == 3:  # ghcr.io/tonc/qinglong
        repo = parts[1]
        dest = f'docker.cnb.cool/{CONFIG["group"]}/{parts[1]}/{"/".join(parts[2:])}'
    elif len(parts) == 4:
        repo = parts[2]
        dest = f'docker.cnb.cool/{CONFIG["group"]}/{parts[2]}/{"/".join(parts[3:])}'
    
    # else:  # ghcr.io/tonc/qinglong
    #     repo = parts[1]
    #     dest = f'docker.cnb.cool/{CONFIG["group"]}/{parts[1]}/{"/".join(parts[2:])}'
    
    ensure_repo_exists(repo)
    return (line, dest)

def main():
    """主执行逻辑"""
    try:
        # 验证配置
        if not all(CONFIG.values()):
            raise ValueError("必须设置 DOCKER_REGISTRY_TOKEN 和 DOCKER_REGISTRY_GROUP 环境变量")

        # 处理镜像列表
        image_file = Path('images.txt')
        if not image_file.exists():
            raise FileNotFoundError("缺少 images.txt 文件")

        with open(image_file) as f:
            images = [line.strip() for line in f if line.strip()]
        
        if not images:
            logger.warning("镜像列表为空")
            return

        success = 0
        for line in images:
            # 去除行首尾空白字符
            line = line.strip()

            # 跳过空行和注释行
            if not line or line.startswith('#'):
                continue
                
            try:
                result = process_image_line(line)
                if not result:
                    continue
                
                src, dest = result
                if copy_image(src, dest):
                    success += 1
            except Exception as e:
                logger.error(f"处理镜像失败: {line} - {str(e)}")

        # logger.info(f"同步完成: 成功 {success}/{len(images)}")
        # if success != len(images):
        #     sys.exit(1)

    except Exception as e:
        logger.error(f"执行失败: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main()
