import argparse
import os
import json
import shutil
import logging
import subprocess
import random
from datetime import datetime
from search import search_video_by_text
from config import POSITIVE_THRESHOLD, NEGATIVE_THRESHOLD

# 获取当前脚本所在的目录
current_dir = os.path.dirname(os.path.abspath(__file__))

# 指定 FFmpeg 的路径（假设 FFmpeg 文件夹在根目录）
FFMPEG_PATH = os.path.join(current_dir, 'ffmpeg', 'bin')

# 将 FFmpeg 路径添加到系统 PATH
os.environ["PATH"] = FFMPEG_PATH + os.pathsep + os.environ["PATH"]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 验证 FFmpeg 是否可用
try:
    subprocess.run(['ffmpeg', '-version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
    logging.info("FFmpeg 已成功添加到 PATH 并可用")
except subprocess.CalledProcessError:
    logging.error("FFmpeg 命令执行失败，请检查安装")
except FileNotFoundError:
    logging.error("找不到 FFmpeg，请确保 FFmpeg 文件夹位于正确位置")

def copy_audio_and_srt_files(input_file, output_folder):
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    input_dir = os.path.dirname(input_file)
    
    # 复制音频文件
    for ext in ['.mp3', '.MP3', '.wav', '.WAV']:
        audio_file = os.path.join(input_dir, base_name + ext)
        if os.path.exists(audio_file):
            destination = os.path.join(output_folder, os.path.basename(audio_file))
            shutil.copy2(audio_file, destination)
            logging.info(f"已复制音频文件: {destination}")
            break
    else:
        logging.warning(f"未找到对应的音频文件: {base_name}")
    
    # 复制 SRT 文件
    srt_file = os.path.join(input_dir, base_name + '.srt')
    if os.path.exists(srt_file):
        destination = os.path.join(output_folder, os.path.basename(srt_file))
        shutil.copy2(srt_file, destination)
        logging.info(f"已复制 SRT 文件: {destination}")
    else:
        logging.warning(f"未找到对应的 SRT 文件: {base_name}.srt")

def time_to_seconds(time_str):
    h, m, s = time_str.split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)

def merge_videos_with_srt(folder_path):
    srt_file = None
    videos = []
    audio_file = None

    # 查找 SRT 文件和视频文件
    for file in os.listdir(folder_path):
        if file.endswith('.srt'):
            srt_file = os.path.join(folder_path, file)
        elif file.endswith('.mp4'):
            videos.append(os.path.join(folder_path, file))
        elif file.endswith(('.mp3', '.wav')):
            audio_file = os.path.join(folder_path, file)

    if not srt_file or not videos or not audio_file:
        logging.error(f"在文件夹 {folder_path} 中未找到 SRT 文件、视频文件或音频文件")
        return

    # 读取 SRT 文件
    with open(srt_file, 'r', encoding='utf-8') as f:
        srt_content = f.read().split('\n\n')

    # 准备 ffmpeg 命令
    output_file = os.path.join(folder_path, f"{os.path.basename(folder_path)}_merged.mp4")
    ffmpeg_command = ['ffmpeg', '-y']
    filter_complex = []

    for i, video in enumerate(videos):
        ffmpeg_command.extend(['-i', video])
        filter_complex.append(f'[{i}:v]setpts=PTS-STARTPTS[v{i}]')

    # 连接所有视频片段
    filter_complex.append(''.join(f'[v{i}]' for i in range(len(videos))) + f'concat=n={len(videos)}:v=1:a=0[outv]')

    # 添加音频
    ffmpeg_command.extend(['-i', audio_file])
    filter_complex.append(f'[outv][{len(videos)}:a]concat=n=1:v=1:a=1[outv][outa]')

    ffmpeg_command.extend(['-filter_complex', ';'.join(filter_complex)])
    ffmpeg_command.extend(['-map', '[outv]', '-map', '[outa]'])

    # 设置输出时长
    audio_duration = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', audio_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout)
    total_duration = audio_duration + 0.3  # 音频时长加300毫秒
    ffmpeg_command.extend(['-t', str(total_duration)])

    ffmpeg_command.append(output_file)

    # 执行 ffmpeg 命令
    logging.info(f"执行 FFmpeg 命令: {' '.join(ffmpeg_command)}")
    try:
        result = subprocess.run(ffmpeg_command, check=True, capture_output=True, text=True, encoding='utf-8')
        logging.info(f"FFmpeg 输出: {result.stdout}")
        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            logging.info(f"视频合并成功，输出文件：{output_file}")
            # 删除原始视频文件
            for video in videos:
                try:
                    os.remove(video)
                    logging.info(f"已删除原始视频文件：{video}")
                except Exception as e:
                    logging.error(f"删除文件 {video} 时出错: {str(e)}")
            return output_file
        else:
            logging.error(f"视频合并失败，输出文件不存在或大小为0：{output_file}")
    except subprocess.CalledProcessError as e:
        logging.error(f"FFmpeg 命令执行失败: {e}")
        logging.error(f"FFmpeg 错误输出: {e.stderr}")

    return None

def process_single_file(input_file, output_folder, top_n):
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    temp_metadata_folder = os.path.join(output_folder, f"{base_name}_temp")
    video_output_folder = os.path.join(output_folder, base_name)
    
    os.makedirs(temp_metadata_folder, exist_ok=True)
    os.makedirs(video_output_folder, exist_ok=True)

    with open(input_file, 'r', encoding='utf-8') as f:
        search_terms = f.read().splitlines()

    copied_videos = []
    for i, term in enumerate(search_terms, 1):
        logging.info(f"搜索第 {i} 个关键词: {term}")
        results = search_video_by_text(term, "", POSITIVE_THRESHOLD, NEGATIVE_THRESHOLD)

        if results and len(results) > 0:
            for j, result in enumerate(results[:top_n], 1):
                metadata = {
                    "search_term": term,
                    "video_path": result['path'],
                    "start_time": result['start_time'],
                    "end_time": result['end_time'],
                    "score": result['score']
                }
                metadata_filename = f"{i}_{j}_{term.replace(' ', '_')}_metadata.json"
                metadata_path = os.path.join(temp_metadata_folder, metadata_filename)
                
                # 保存元数据
                with open(metadata_path, 'w', encoding='utf-8') as mf:
                    json.dump(metadata, mf, ensure_ascii=False, indent=2)
                logging.info(f"已保存搜索结果 {j} 的元数据: {metadata_path}")
                
                # 复制视频文件
                source_path = metadata['video_path']
                if os.path.exists(source_path):
                    video_filename = f"{i}_{term.replace(' ', '_')}.mp4"
                    destination_path = os.path.join(video_output_folder, video_filename)
                    shutil.copy2(source_path, destination_path)
                    logging.info(f"已复制视频: {destination_path}")
                    copied_videos.append(destination_path)
                else:
                    logging.error(f"视频文件不存在: {source_path}")
                
                # 删除JSON文件
                os.remove(metadata_path)
                logging.info(f"已删除元数据文件: {metadata_path}")
        else:
            logging.warning(f"未找到与 '{term}' 相关的视频")
    
    # 复制对应的音频文件和 SRT 文件
    copy_audio_and_srt_files(input_file, video_output_folder)
    
    # 删除临时元数据文件夹
    shutil.rmtree(temp_metadata_folder)
    logging.info(f"已删除临时元数据文件夹: {temp_metadata_folder}")
    
    # 在处理完单个文件后，立即调用视频合并函数
    merged_video = merge_videos_with_srt(video_output_folder)
    if merged_video:
        logging.info(f"成功合并视频：{merged_video}")
    else:
        logging.error("视频合并失败，保留原始视频文件")

    return copied_videos

def process_input(input_path, output_folder, top_n):
    total_copied_videos = 0
    if os.path.isfile(input_path) and input_path.endswith('.txt'):
        logging.info(f"处理单个文件: {input_path}")
        copied_videos = process_single_file(input_path, output_folder, top_n)
        total_copied_videos += len(copied_videos)
    elif os.path.isdir(input_path):
        for filename in os.listdir(input_path):
            if filename.endswith('.txt'):
                file_path = os.path.join(input_path, filename)
                logging.info(f"处理文件: {file_path}")
                copied_videos = process_single_file(file_path, output_folder, top_n)
                total_copied_videos += len(copied_videos)
    else:
        logging.error(f"无效的输入路径: {input_path}")
    return total_copied_videos

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="批量搜索视频并保存结果")
    parser.add_argument("input_path", help="包含搜索关键词的txt文件路径或包含txt文件的文件夹路径")
    parser.add_argument("output_folder", help="保存视频文件的根文件夹路径")
    parser.add_argument("--top_n", type=int, default=1, help="每个关键词保存的视频数量（默认为1）")

    args = parser.parse_args()

    total_copied_videos = process_input(args.input_path, args.output_folder, args.top_n)
    logging.info(f"总共处理了 {total_copied_videos} 个视频文件")