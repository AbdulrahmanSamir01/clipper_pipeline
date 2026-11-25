import os
import json
import glob
import subprocess
from .effects import VideoEffectsProcessor
from .transitions import VideoTransitionManager
from .subtitles import SubtitleProcessor


class VideoPipelineAutomation:
    def __init__(self, source_parent_dir, srt_dir, md_dir, json_config_path, output_dir):
        self.source_parent_dir = source_parent_dir
        self.srt_dir = srt_dir
        self.md_dir = md_dir  
        self.output_dir = output_dir
        
        with open(json_config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
            
        self.transition_type = self.config.get("transition_type", "fade")
        
        # Initialize modules
        self.effects_processor = VideoEffectsProcessor(self.config)
        self.transition_manager = VideoTransitionManager(self.config)
        self.subtitle_processor = SubtitleProcessor(self.config, self.md_dir)
        
        os.makedirs(self.output_dir, exist_ok=True)

    def process_all_folders(self):
        if not os.path.exists(self.source_parent_dir):
            print(f"[-] Main source directory not found: {self.source_parent_dir}")
            return
            
        subfolders = [f.path for f in os.scandir(self.source_parent_dir) if f.is_dir()]
        print(f"[*] Found {len(subfolders)} folders to process.")
        
        for folder in subfolders:
            folder_name = os.path.basename(folder)
            print(f"\n[*] Starting processing folder: {folder_name}")
            self.process_single_folder(folder, folder_name)

    def run_ffmpeg_command(self, inputs_cmd, filter_parts, audio_file, final_video_stream, output_file, video_files):
        audio_input_idx = len(inputs_cmd) // 2
        inputs_cmd.extend(["-i", audio_file])
        
        if self.transition_type == "crossfade":
            video_durations = [self.transition_manager.get_video_duration(v) for v in video_files]
            total_duration = sum(video_durations) - ((len(video_files) - 1) * self.transition_manager.transition_duration)
            filter_parts.append(
                f"[{audio_input_idx}:a]atrim=duration={total_duration},asetpts=PTS-STARTPTS[a_final]"
            )
            audio_map_str = "[a_final]"
        else:
            audio_map_str = f"{audio_input_idx}:a"
        
        filter_complex_str = ";\n".join(filter_parts)
        
        cmd = ["ffmpeg", "-y"] + inputs_cmd + [
            "-filter_complex", filter_complex_str,
            "-map", f"[{final_video_stream}]",
            "-map", audio_map_str,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "18",
            "-c:a", "aac",
            output_file
        ]
        
        print(f"[*] Running FFmpeg processing and exporting (Transition: {self.transition_type})...")
        
        try:
            # Hide FFmpeg output in normal mode, capture stderr to show only if an error occurs
            subprocess.run(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True, 
                check=True
            )
            print(f"[✔] Successfully saved video to: {output_file}")
            
        except subprocess.CalledProcessError as e:
            print(f"[-] Error: FFmpeg failed to process the video!")
            print(f"[-] FFmpeg Error Output:\n{e.stderr}")

    def process_single_folder(self, folder_path, folder_name):
        output_file = os.path.join(self.output_dir, f"{folder_name}.mp4")
        
        # Skip feature: Check if the final output file already exists
        if os.path.exists(output_file):
            print(f"[i] Skipping folder '{folder_name}' because the output file already exists.")
            return

        video_files = sorted(
            glob.glob(os.path.join(folder_path, "*.mp4")) + 
            glob.glob(os.path.join(folder_path, "*.mov")),
            key=lambda x: int(''.join(filter(str.isdigit, os.path.basename(x))) or 0)
        )
        audio_files = glob.glob(os.path.join(folder_path, "*.mp3")) + glob.glob(os.path.join(folder_path, "*.wav"))
        srt_path = os.path.join(self.srt_dir, f"{folder_name}.srt")
        
        if not video_files or not audio_files or not os.path.exists(srt_path):
            print(f"[-] Warning: Missing components (videos, audio, or srt) for folder {folder_name}. Skipped.")
            return

        audio_file = audio_files[0]
        sub_img_dir = os.path.join(folder_path, "sub_temp_images")
        
        # 1. Generate subtitles
        subtitles = self.subtitle_processor.create_subtitle_images(srt_path, sub_img_dir, folder_name)
        
        # 2. Apply video filters and colors
        inputs_cmd, filter_parts = self.effects_processor.apply_video_filters(video_files)
        
        # 3. Apply transitions and merge videos
        last_video_stream = self.transition_manager.apply_transitions(video_files, filter_parts)
        
        # 4. Overlay subtitles layer
        last_video_stream, inputs_cmd = self.subtitle_processor.apply_subtitles_overlay(last_video_stream, subtitles, inputs_cmd, filter_parts)
        
        # 5. Final export via FFmpeg
        self.run_ffmpeg_command(inputs_cmd, filter_parts, audio_file, last_video_stream, output_file, video_files)