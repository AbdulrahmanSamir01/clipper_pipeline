import os
import pysrt
from PIL import Image, ImageDraw, ImageFont

class SubtitleProcessor:
    def __init__(self, config, md_dir):
        self.config = config
        self.md_dir = md_dir
        self.fonts_dir = self.config.get("fonts_dir", "./fonts")
        # دعم الأبعاد الديناميكية المتغيرة
        self.target_width = self.config.get("target_width", 1080)
        self.target_height = self.config.get("target_height", 1920)

    def extract_font_from_md(self, folder_name):
        default_font = self.config.get("font_name", "arial.ttf")
        if not self.md_dir:
            return default_font
            
        md_path = os.path.join(self.md_dir, f"{folder_name}.md")
        if not os.path.exists(md_path):
            return default_font
            
        try:
            with open(md_path, 'r', encoding='utf-8') as f:
                content = f.read()
            start_tag, end_tag = "[font]", "[/font]"
            if start_tag in content and end_tag in content:
                start_idx = content.find(start_tag) + len(start_tag)
                end_idx = content.find(end_tag, start_idx)
                extracted_font = content[start_idx:end_idx].strip()
                if extracted_font:
                    return extracted_font
        except Exception as e:
            print(f"[-] خطأ أثناء قراءة ملف الـ MD: {e}")
        return default_font

    def get_font_file_path(self, font_name):
        clean_name = font_name.strip()
        direct_path = os.path.join(self.fonts_dir, clean_name)
        if os.path.exists(direct_path):
            return direct_path

        for ext in [".ttf", ".otf", ".TTF", ".OTF"]:
            path_with_ext = os.path.join(self.fonts_dir, clean_name + ext)
            if os.path.exists(path_with_ext):
                return path_with_ext

        if os.path.exists(self.fonts_dir):
            try:
                for f in os.listdir(self.fonts_dir):
                    base_f_name, _ = os.path.splitext(f)
                    if clean_name.lower() in base_f_name.lower() or base_f_name.lower() in clean_name.lower():
                        return os.path.join(self.fonts_dir, f)
            except Exception:
                pass

        if os.path.exists(clean_name):
            return clean_name
        return clean_name

    def create_subtitle_images(self, srt_path, output_img_dir, folder_name):
        os.makedirs(output_img_dir, exist_ok=True)

        subs = pysrt.open(srt_path, encoding='utf-8')

        subtitle_data = []
        base_reference_width = 1080  
        base_config_font_size = self.config.get("font_size", 45) 
        
        scale_ratio = self.target_width / base_reference_width  
        font_size = max(12, int(base_config_font_size * scale_ratio))  
        stroke_width = max(1, int(font_size * 0.05)) 

        target_font_name = self.extract_font_from_md(folder_name)  
        font_path = self.get_font_file_path(target_font_name)
        
        try:  
            font = ImageFont.truetype(font_path, font_size)  
        except:  
            try:  
                font = ImageFont.truetype("arial.ttf", font_size)  
            except:  
                font = ImageFont.load_default()

        try:  
            ascent, descent = font.getmetrics()  
        except:  
            ascent, descent = font_size * 0.8, font_size * 0.2
            
        line_height = ascent + descent

        max_text_width = max(10, self.target_width - int(self.target_width * 0.1))

        for i, sub in enumerate(subs):  
            raw_text = sub.text.replace('\n', ' ')  
            words = raw_text.split()  
            lines, current_line = [], []
            
            dummy_img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))  
            draw = ImageDraw.Draw(dummy_img)
            
            for word in words:  
                test_line = " ".join(current_line + [word])  
                try:  
                    bbox = draw.textbbox((0, 0), test_line, font=font, direction="rtl")  
                except:  
                    bbox = draw.textbbox((0, 0), test_line, font=font)
                    
                if (bbox[2] - bbox[0]) <= max_text_width or not current_line:  
                    current_line.append(word)  
                else:  
                    lines.append(" ".join(current_line))  
                    current_line = [word]  
            if current_line:  
                lines.append(" ".join(current_line))

            final_text = "\n".join(lines)
            
            try:  
                bbox = draw.multiline_textbbox((0, 0), final_text, font=font, align="center", direction="rtl")  
            except:  
                bbox = draw.multiline_textbbox((0, 0), final_text, font=font, align="center")
                
            text_w = bbox[2] - bbox[0]
            
            total_text_h = len(lines) * line_height  
            padding_y = int(font_size * 0.8)  
            img_height = int(total_text_h + padding_y)
            
            start_sec, end_sec = sub.start.ordinal / 1000.0, sub.end.ordinal / 1000.0  
            img_filename = os.path.join(output_img_dir, f"sub_{i:04d}.png")
            
            sub_img = Image.new("RGBA", (self.target_width, img_height), (0, 0, 0, 0))  
            sub_draw = ImageDraw.Draw(sub_img)  
            x_pos = (self.target_width - text_w) / 2  
            y_pos_text = (img_height - total_text_h) / 2
            
            try:  
                sub_draw.multiline_text((x_pos, y_pos_text), final_text, font=font, fill="white", align="center", stroke_width=stroke_width, stroke_fill="black", direction="rtl")  
            except:  
                sub_draw.multiline_text((x_pos, y_pos_text), final_text, font=font, fill="white", align="center", stroke_width=stroke_width, stroke_fill="black")
            
            sub_img.save(img_filename, "PNG")
            
            vertical_position_ratio = self.config.get("subtitle_position_ratio", 0.85)
            base_y_pos = self.target_height * vertical_position_ratio
            y_pos = base_y_pos - (img_height / 2)
            
            subtitle_data.append({"img": img_filename, "start": start_sec, "end": end_sec, "y": y_pos})
            
        return subtitle_data

    def apply_subtitles_overlay(self, last_video_stream, subtitles, inputs_cmd, filter_parts):
        for idx, sub in enumerate(subtitles):
            sub_input_idx = len(inputs_cmd) // 2  
            inputs_cmd.extend(["-i", sub["img"]])
            next_video_stream = f"v_sub_{idx}"
            enable_cond = f"between(t,{sub['start']},{sub['end']})"
            
            filter_parts.append(
                f"[{last_video_stream}][{sub_input_idx}:v]overlay=x=(W-w)/2:y={sub['y']}:enable='{enable_cond}'[{next_video_stream}]"
            )
            last_video_stream = next_video_stream
            
        return last_video_stream, inputs_cmd