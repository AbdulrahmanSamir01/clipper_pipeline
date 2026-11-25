import subprocess

class VideoTransitionManager:
    def __init__(self, config):
        self.config = config
        self.transition_duration = self.config.get("transition_duration", 0.8)
        self.transition_type = self.config.get("transition_type", "fade")

    def get_video_duration(self, vfile):
        """جلب المدة الزمنية الحقيقية للفيديو بدقة عالية باستخدام ffprobe"""
        try:
            cmd = [
                "ffprobe", "-v", "error", 
                "-show_entries", "format=duration", 
                "-of", "default=noprint_wrappers=1:nokey=1", 
                vfile
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            return float(result.stdout.strip())
        except Exception as e:
            print(f"[-] خطأ في قراءة مدة الفيديو {vfile}: {e}")
            return 0.0

    def apply_transitions(self, video_files, filter_parts):
        """اختيار وتطبيق الانتقال المناسب بناءً على الإعدادات"""
        if len(video_files) <= 1:
            return "v0"

        if self.transition_type == "crossfade":
            return self._apply_smooth_transitions(video_files, filter_parts)
        elif self.transition_type == "fade":
            return self._apply_fade_transitions(video_files, filter_parts)
        else:
            return self._apply_none_concat(video_files, filter_parts)

    def _apply_smooth_transitions(self, video_files, filter_parts):
        video_durations = [self.get_video_duration(v) for v in video_files]
        current_v = "v0"
        fps = 30.0
        frame_duration = 1.0 / fps

        raw_offset = video_durations[0] - self.transition_duration
        current_offset = round(raw_offset / frame_duration) * frame_duration

        for idx in range(1, len(video_files)):
            next_v = f"v{idx}"
            is_last = (idx == len(video_files) - 1)
            merged_v = "v_final_merged" if is_last else f"v_merged_{idx}"
            
            offset_time = max(frame_duration, current_offset)

            filter_parts.append(
                f"[{current_v}][{next_v}]xfade=transition=fade:duration={self.transition_duration}:offset={offset_time}[{merged_v}]"
            )
            
            if not is_last:
                raw_next_offset = offset_time + video_durations[idx] - self.transition_duration
                current_offset = round(raw_next_offset / frame_duration) * frame_duration
                current_v = merged_v
            else:
                current_v = merged_v
            
        return current_v

    def _apply_fade_transitions(self, video_files, filter_parts):
        video_durations = [self.get_video_duration(v) for v in video_files]
        processed_streams = []

        for idx, duration in enumerate(video_durations):
            v_in = f"v{idx}"
            v_out = f"v_faded_{idx}"
            fade_out_start = max(0, duration - self.transition_duration)
            
            filter_parts.append(
                f"[{v_in}]fade=t=in:st=0:d={self.transition_duration},fade=t=out:st={fade_out_start}:d={self.transition_duration}[{v_out}]"
            )
            processed_streams.append(f"[{v_out}]")

        concat_inputs = "".join(processed_streams)
        filter_parts.append(f"{concat_inputs}concat=n={len(video_files)}:v=1:a=0[v_final_merged]")
        return "v_final_merged"

    def _apply_none_concat(self, video_files, filter_parts):
        processed_streams = [f"[v{idx}]" for idx in range(len(video_files))]
        concat_inputs = "".join(processed_streams)
        filter_parts.append(f"{concat_inputs}concat=n={len(video_files)}:v=1:a=0[v_final_merged]")
        return "v_final_merged"