import os

class VideoEffectsProcessor:
    def __init__(self, config):
        self.config = config
        # الاعتماد على القيم المرنة أو الافتراضية الذكية مع عدم الإجبار بقيم صلبة غير مرنة
        self.target_width = self.config.get("target_width", None)
        self.target_height = self.config.get("target_height", None)
        
        # التأثيرات: إذا كانت القيمة 0، يعني إيقاف التأثير
        self.contrast_val = self.config.get("contrast", 1.0)
        self.saturation_val = self.config.get("saturation", 1.0)
        self.brightness_val = self.config.get("brightness", 0.0)

    def apply_video_filters(self, video_files, input_width=1080, input_height=1920):
        """تجهيز أوامر الإدخال وتطبيق فلاتر التحجيم الديناميكي وإيقاف التأثيرات عند الصفر"""
        # إذا لم يتم تحديد الأبعاد في الإعدادات، نأخذ أبعاد أول فيديو أو القيم الافتراضية المتناسقة
        w = self.target_width if self.target_width else input_width
        h = self.target_height if self.target_height else input_height

        inputs_cmd = []
        filter_parts = []
        
        # بناء سلسلة فلاتر الألوان (eq) ديناميكياً بناءً على ما إذا كانت مفعلة أم لا
        eq_params = []
        if self.contrast_val != 0:
            eq_params.append(f"contrast={self.contrast_val}")
        if self.saturation_val != 0:
            eq_params.append(f"saturation={self.saturation_val}")
        if self.brightness_val != 0:
            eq_params.append(f"brightness={self.brightness_val}")
            
        eq_filter = f"eq={':'.join(eq_params)}," if eq_params else ""

        for idx, vfile in enumerate(video_files):
            inputs_cmd.extend(["-i", vfile])
            filter_parts.append(
                f"[{idx}:v]fps=30,"
                f"scale=w='if(gt(iw/ih,{w}/{h}),-2,{w})':"
                f"h='if(gt(iw/ih,{w}/{h}),{h},-2)',"
                f"crop={w}:{h}:(in_w-{w})/2:(in_h-{h})/2,"
                f"{eq_filter}setsar=1[v{idx}]"
            )
        return inputs_cmd, filter_parts