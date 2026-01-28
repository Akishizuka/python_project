import cv2
import mediapipe as mp
import numpy as np
import time
import os
from collections import deque
from PIL import Image, ImageDraw, ImageFont

# ==========================================================
# 1. 核心工具类 (数据平滑与渲染)
# ==========================================================
class SensitivityFilter:
    def __init__(self, alpha=0.1): 
        # alpha=0.1: 非常平滑，抗抖动能力极强
        self.alpha = alpha
        self.data = {}
        
    def process(self, raw_dict):
        for k, v in raw_dict.items():
            if k not in self.data: 
                self.data[k] = v
            else: 
                self.data[k] = self.alpha * v + (1 - self.alpha) * self.data[k]
        return self.data

def super_remap(val, low=0.08, high=0.6): 
    # low=0.08: 忽略 8% 以下的微小肌肉抽动 (死区)
    # high=0.6: 需要做大概 60% 幅度的动作才能拿满分
    if val < low: return 0.0
    if val > high: return 1.0
    return (val - low) / (high - low)

class ChineseRenderer:
    def __init__(self, size=35):
        # 尝试加载常见的中文字体
        font_paths = [
            "C:/Windows/Fonts/msyh.ttc",   # Windows 微软雅黑
            "C:/Windows/Fonts/simhei.ttf", # Windows 黑体
            "/System/Library/Fonts/PingFang.ttc", # Mac
            "simhei.ttf" # 当前目录
        ]
        self.font = None
        for path in font_paths:
            if os.path.exists(path):
                try:
                    self.font = ImageFont.truetype(path, size)
                    self.small_font = ImageFont.truetype(path, 18)
                    print(f"🎉 成功加载字体: {path}")
                    break
                except: continue
        
        # 如果找不到字体，使用默认（可能不支持中文显示）
        if self.font is None:
            print("⚠️ 未找到中文字体，将使用默认字体")
            self.font = ImageFont.load_default()
            self.small_font = ImageFont.load_default()

    def draw(self, img, text, pos, color=(255, 255, 255), is_small=False):
        img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        f = self.small_font if is_small else self.font
        x, y = pos
        # 描边效果
        for ox, oy in [(-1,-1), (1,-1), (-1,1), (1,1)]:
            draw.text((x+ox, y+oy), text, font=f, fill=(0,0,0))
        draw.text(pos, text, font=f, fill=color)
        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

# ==========================================================
# 2. 表情逻辑引擎 V9 (高稳定性版)
# ==========================================================
class ExpressionExpertV9:
    def __init__(self):
        self.base_line = {}
        self.calibrated = False
        self.calib_frames = []
        self.filter = SensitivityFilter(alpha=0.3) # 提高alpha，响应更即时

    def calibrate(self, raw):
        self.calib_frames.append(raw)
        # 减少校准帧数，加快校准速度（从50降到20）
        calib_frames_needed = 20
        if len(self.calib_frames) >= calib_frames_needed:
            self.base_line = {k: sum(f[k] for f in self.calib_frames)/len(self.calib_frames) for k in raw.keys()}
            self.calibrated = True
            return True
        return False

    def analyze(self, raw_input):
        raw = self.filter.process(raw_input)
        if not self.calibrated:
            self.calibrate(raw)
            return f"校准中...", (200, 200, 200), {}, False

        d = {k: max(0, raw[k] - self.base_line.get(k, 0)) for k in raw.keys()}
        
        # --- 1. 核心特征组提取 ---
        b_inner = d.get('browInnerUp', 0)    # AU1 (眉头)
        b_outer = (d.get('browOuterUpLeft', 0) + d.get('browOuterUpRight', 0)) / 2 # AU2 (眉梢)
        b_down = (d.get('browDownLeft', 0) + d.get('browDownRight', 0)) / 2       # AU4 (压眉)
        
        e_wide = (d.get('eyeWideLeft', 0) + d.get('eyeWideRight', 0)) / 2          # AU5 (瞪眼)
        e_squint = (d.get('eyeSquintLeft', 0) + d.get('eyeSquintRight', 0)) / 2    # AU7 (眯眼)
        
        m_smile = (d.get('mouthSmileLeft', 0) + d.get('mouthSmileRight', 0)) / 2   # AU12 (嘴角上扬)
        m_frown = (d.get('mouthFrownLeft', 0) + d.get('mouthFrownRight', 0)) / 2   # AU15 (嘴角下撇)
        m_stretch = (d.get('mouthStretchLeft', 0) + d.get('mouthStretchRight', 0)) / 2 # AU20 (口角平拉)
        m_shrug = d.get('mouthShrugUpper', 0)                                      # AU17 (下巴皱缩)
        n_sneer = (d.get('noseSneerLeft', 0) + d.get('noseSneerRight', 0)) / 2     # AU9 (皱鼻)
        jaw = d.get('jawOpen', 0)                                                  # AU26/27 (张嘴)
        
        # 难过专用：嘴角下撇、下巴皱缩的原始值（绝对值），用于在增量很小时仍能识别
        m_frown_raw = (raw.get('mouthFrownLeft', 0) + raw.get('mouthFrownRight', 0)) / 2
        m_shrug_raw = raw.get('mouthShrugUpper', 0)

        s = {}

        # --- 2. 逻辑博弈引擎 ---

        # 【难过 😢】核心：嘴角下撇 + 下巴皱缩 + 眉毛紧张(八字眉/皱眉)，权重放大，便于判出
        # 同时用增量 d 和原始值 raw：增量小但原始值高时也能判难过（如基线嘴角就偏下）
        
        # 难过嘴型：取「增量」与「原始值×0.6」的较大者，避免校准后增量过小判不出
        frown_eff = max(m_frown, m_frown_raw * 0.6)
        shrug_eff = max(m_shrug, m_shrug_raw * 0.5)
        
        # 八字眉/皱眉辅助：内提或下压（无皱鼻时皱眉也可能是难过）
        sad_brow_score = 0.0
        if b_inner > 0.04:
            sad_brow_outer_penalty = max(0, b_outer - 0.05) * 3.0
            sad_brow_down_penalty = max(0, b_down - 0.18) * 1.2
            sad_brow_score = max(0, (b_inner * 2.5) - sad_brow_outer_penalty - sad_brow_down_penalty)
        # 皱眉(b_down)在无皱鼻时也可作为难过辅助
        if n_sneer < 0.1 and b_down > 0.06 and b_outer < 0.15:
            sad_brow_score += b_down * 1.5
        
        # 难过综合评分：权重大幅提高，让难过分在数值偏小时也能上去
        s['难过 😢'] = (frown_eff * 12.0 + shrug_eff * 8.0 + sad_brow_score * 5.0)
        
        # 嘴角下撇/下巴皱缩明显时加成
        if frown_eff > 0.1: s['难过 😢'] *= 1.4
        if frown_eff > 0.2: s['难过 😢'] *= 1.3
        if shrug_eff > 0.1: s['难过 😢'] *= 1.2
        
        # 排除干扰：快乐(笑容)、惊讶(外眉梢抬起)、愤怒(皱鼻)
        s['难过 😢'] /= (1.0 + m_smile * 8.0 + b_outer * 3.0 + n_sneer * 6.0)

        # 【惊讶 😲】核心：全眉毛上扬 + 垂直掉下巴
        # 修正：必须有外眉梢带动。如果嘴角在往两边平拉(m_stretch)，那是恐惧而非惊讶
        s['惊讶 😲'] = (b_outer * 5.0 + b_inner * 2.0 + jaw * 5.0)
        s['惊讶 😲'] /= (1.0 + m_stretch * 4.0 + b_down * 3.0)

        # 【恐惧 😱】核心：瞪眼 + 嘴角平拉 (尖叫感)
        # 特征：眼睛睁大(e_wide高) + 嘴角向两边平拉(m_stretch高) + 眉毛可能上提
        # 权重大幅提高，便于判出
        fear_eye = e_wide  # 瞪眼是关键
        fear_mouth = m_stretch  # 嘴角平拉是关键
        
        s['恐惧 😱'] = (fear_eye * 10.0 + fear_mouth * 8.0 + b_inner * 2.5)
        
        # 瞪眼或嘴角平拉明显时加成
        if e_wide > 0.08: s['恐惧 😱'] *= 1.3
        if m_stretch > 0.1: s['恐惧 😱'] *= 1.2
        if e_wide > 0.12 and m_stretch > 0.08: s['恐惧 😱'] *= 1.4  # 两个关键特征都有时大幅加成
        
        # 排除干扰：惊讶(全眉毛上扬+张嘴)、开心(笑容)
        s['恐惧 😱'] /= (1.0 + jaw * 2.0 + m_smile * 4.0)
        
        # 如果没有瞪眼，恐惧分衰减（但不要太严格，避免判不出）
        if e_wide < 0.06: s['恐惧 😱'] *= 0.3  # 从0.1放宽到0.3
        # 如果既没有瞪眼也没有嘴角平拉，恐惧分大幅降低
        if e_wide < 0.06 and m_stretch < 0.08: s['恐惧 😱'] *= 0.15

        # 【愤怒 😡】核心：压眉 + 皱鼻 + 眯眼紧盯
        # 关键特征：皱鼻(n_sneer)是愤怒的独特特征；难过只有嘴角下撇，很少皱鼻
        # 权重大幅提高，便于判出
        anger_brow = b_down  # 压眉是关键特征
        anger_nose = n_sneer  # 皱鼻是关键特征（这是愤怒的独特特征）
        anger_eye = e_squint  # 眯眼
        
        s['愤怒 😡'] = (anger_brow * 8.0 + anger_nose * 10.0 + anger_eye * 4.0)
        
        # 压眉或皱鼻明显时加成
        if b_down > 0.08: s['愤怒 😡'] *= 1.3
        if n_sneer > 0.1: s['愤怒 😡'] *= 1.4
        if b_down > 0.1 and n_sneer > 0.08: s['愤怒 😡'] *= 1.5  # 两个关键特征都有时大幅加成
        
        # 嘴角下撇时：若同时有皱鼻则可能是愤怒，若无皱鼻则多半是难过，要压制愤怒分
        if m_frown > 0.12 and n_sneer < 0.1:
            s['愤怒 😡'] /= (1.0 + m_frown * 6.0)  # 嘴角下撇明显且无皱鼻 → 难过，强烈压制愤怒
        elif m_frown > 0.1 and n_sneer > 0.08:
            s['愤怒 😡'] += m_frown * 2.0  # 有皱鼻时嘴角下撇增强愤怒
        
        # 排除干扰：惊讶(外眉梢)、开心(笑容)、难过(内提明显 或 嘴角下撇明显但无皱鼻)
        sad_brow_ratio = b_inner / (b_down + 0.01)
        if sad_brow_ratio > 1.2: s['愤怒 😡'] /= (1.0 + b_inner * 4.0)  # 八字眉倾向 → 难过
        
        s['愤怒 😡'] /= (1.0 + b_outer * 5.0 + m_smile * 4.0)
        
        # 必要条件检查放宽：只要有压眉或皱鼻之一，就不大幅衰减
        if b_down < 0.06 and n_sneer < 0.06: s['愤怒 😡'] *= 0.4  # 两个都没有才衰减
        elif b_down < 0.06: s['愤怒 😡'] *= 0.7  # 只有压眉低，衰减较轻
        elif n_sneer < 0.06: s['愤怒 😡'] *= 0.6  # 只有皱鼻低，衰减较轻

        # 【开心 😊】核心：嘴角上扬(m_smile)为主，挤眼角(e_squint)为辅
        # 重要：难过时也会眯眼，所以必须「有笑容时才让眯眼加分」，否则难过会被误判成开心
        # 且一旦有嘴角下撇(m_frown)，开心分必须被强烈压制
        smile_base = m_smile * 6.0
        # 只有嘴角明显上扬时，眯眼才计入开心（真笑=笑+眯眼）；否则眯眼可能是难过/痛苦
        if m_smile > 0.15:
            smile_base += e_squint * 3.0
        else:
            smile_base += e_squint * 0.5  # 无笑容时眯眼几乎不加分，避免难过眯眼被当开心
        
        s['开心 😊'] = smile_base
        
        # 一旦有嘴角下撇，开心分必须被强烈压制（难过/委屈的嘴型与开心相反）
        s['开心 😊'] /= (1.0 + b_down * 2.0 + m_frown * 8.0)  # m_frown 权重大幅提高
        if m_frown > 0.1:  # 只要有明显嘴角下撇，开心分再大幅衰减
            s['开心 😊'] *= 0.2
        if m_frown > 0.18:
            s['开心 😊'] *= 0.15  # 嘴角下撇很明显时，开心分接近清零
        # 嘴角下撇比上扬更明显时，一定是难过/委屈而非开心
        if m_frown > m_smile and m_frown > 0.08:
            s['开心 😊'] *= 0.1

        # --- 3. 动态平静与决策 ---
        # 活跃度：难过相关特征权重要大，一旦有难过特征，平静分应明显被拉低
        activity = sum([b_inner, b_outer, b_down, m_smile, jaw,
                       m_frown * 3.0, m_shrug * 2.5])  # 嘴角下撇、下巴皱缩大幅拉低平静
        s['平静 😐'] = max(0.08, 0.5 - activity)
        
        # 有明显难过特征时，平静分必须被压到很低，避免“明显难过”仍判成平静
        if m_frown > 0.08 or m_shrug > 0.1:
            s['平静 😐'] *= 0.25  # 只要有一点嘴角下撇或下巴皱缩，平静分大幅衰减
        if m_frown > 0.12 or m_shrug > 0.15:
            s['平静 😐'] *= 0.3   # 更明显时再压一层
        # 眉毛紧张(压眉/内提) + 嘴角下撇，且无皱鼻 → 很可能是难过，平静分再降
        if (b_down > 0.08 or b_inner > 0.06) and m_frown > 0.06 and n_sneer < 0.12:
            s['平静 😐'] *= 0.2
        
        winner, win_score = max(s.items(), key=lambda x: x[1])
        
        # 难过信号：嘴角下撇/下巴皱缩/皱眉+撇嘴（放宽阈值，便于判出）
        # frown_eff, shrug_eff 已在上文难过判定中定义
        sad_signals = (frown_eff > 0.06 or shrug_eff > 0.08 or
                       (frown_eff > 0.04 and (b_down > 0.05 or b_inner > 0.04)) or
                       (m_frown_raw > 0.15 and n_sneer < 0.12))  # 原始值嘴角下撇明显且无皱鼻
        
        # 若有明显难过特征且难过分不是极低，优先判难过而不判平静
        if winner == "平静 😐" and sad_signals and s['难过 😢'] > 0.08:
            winner = "难过 😢"
            win_score = s['难过 😢']
        
        # 门槛设置：开心和惊讶稍微提高，避免过于灵敏
        thresholds = {'惊讶 😲': 0.6, '开心 😊': 0.6, '难过 😢': 0.28, '愤怒 😡': 0.25, '恐惧 😱': 0.22}
        final_threshold = thresholds.get(winner, 0.45)
        
        final_name = winner if win_score > final_threshold else "平静 😐"
        
        # 有明显难过特征时，若当前判成平静，则优先改为难过
        if final_name == "平静 😐" and sad_signals and s['难过 😢'] > 0.06:
            final_name = "难过 😢"
        
        # 有明显愤怒特征时，若当前判成平静，则优先改为愤怒
        anger_signals = (n_sneer > 0.08 or (b_down > 0.08 and n_sneer > 0.05))
        if final_name == "平静 😐" and anger_signals and s['愤怒 😡'] > 0.12:
            final_name = "愤怒 😡"
        
        # 有明显恐惧特征时，若当前判成平静，则优先改为恐惧
        fear_signals = (e_wide > 0.08 or (e_wide > 0.06 and m_stretch > 0.08))
        if final_name == "平静 😐" and fear_signals and s['恐惧 😱'] > 0.12:
            final_name = "恐惧 😱"
        
        # 兜底：嘴角下撇或下巴皱缩原始值明显偏高且无笑容、无皱鼻，直接判难过
        if (final_name == "平静 😐" and m_smile < 0.15 and n_sneer < 0.1 and
            (m_frown_raw > 0.2 or m_shrug_raw > 0.25)):
            final_name = "难过 😢"

        # 颜色映射
        color_map = {'开心': (0, 255, 255), '愤怒': (0, 0, 255), '惊讶': (0, 165, 255),
                     '难过': (200, 200, 200), '恐惧': (128, 0, 128), '平静': (255, 255, 255)}
        color = next((v for k, v in color_map.items() if k in final_name), (255, 255, 255))

        return final_name, color, s, True

class SensitivityFilter:
    """简易EMA滤波器，平衡灵敏度与抖动"""
    def __init__(self, alpha=0.25):
        self.alpha = alpha
        self.state = None

    def process(self, data):
        if self.state is None:
            self.state = data.copy()
        for k in data:
            self.state[k] = self.alpha * data[k] + (1 - self.alpha) * self.state[k]
        return self.state
# ==========================================================
# 3. 主程序入口
# ==========================================================
def main():
    model_path = 'face_landmarker.task'
    
    # 检查模型是否存在
    if not os.path.exists(model_path):
        print(f"❌ 错误：未找到模型文件 {model_path}")
        print("请从 MediaPipe 官网下载 face_landmarker.task 并放入当前目录。")
        return

    # 初始化 MediaPipe
    BaseOptions = mp.tasks.BaseOptions
    FaceLandmarker = mp.tasks.vision.FaceLandmarker
    FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
    VisionRunningMode = mp.tasks.vision.RunningMode

    options = FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=VisionRunningMode.VIDEO,
        output_face_blendshapes=True,
        num_faces=1
    )

    detector = FaceLandmarker.create_from_options(options)
    cap = cv2.VideoCapture(0)
    
    # 使用 V9 引擎
    engine = ExpressionExpertV9()
    renderer = ChineseRenderer()

    print("摄像头已启动。按 'Q' 退出，按 'R' 重新校准。")

    while cap.isOpened():
        success, frame = cap.read()
        if not success: break
        
        # 镜像翻转
        frame = cv2.flip(frame, 1)
        h, w, _ = frame.shape
        
        # MediaPipe 需要 RGB
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        
        # 侦测 (使用时间戳)
        res = detector.detect_for_video(mp_image, int(time.time() * 1000))

        if res.face_blendshapes:
            # 获取原始数据
            raw_dict = {b.category_name: b.score for b in res.face_blendshapes[0]}
            
            # 引擎分析
            result, color, scores, calibrated = engine.analyze(raw_dict)

            # 绘制 UI
            if not calibrated:
                # 校准阶段
                cv2.rectangle(frame, (w//2-150, h//2-40), (w//2+150, h//2+40), (50,50,50), -1)
                frame = renderer.draw(frame, result, (w//2-120, h//2-20), (0, 255, 0))
            else:
                # 底部信息栏
                cv2.rectangle(frame, (0, h-80), (w, h), (30, 30, 30), -1)
                frame = renderer.draw(frame, f"当前状态：{result}", (30, h-65), color)
                
                # 右侧 Top 5 能量条
                sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
                for i, (name, val) in enumerate(sorted_scores):
                    by = 50 + i*40
                    # 绘制背景条
                    cv2.rectangle(frame, (w-160, by), (w-20, by+15), (50,50,50), -1)
                    # 绘制能量条 (限制最大长度)
                    bw = int(min(val, 2.0) / 2.0 * 140) 
                    bar_color = color if name == result else (150,150,150)
                    cv2.rectangle(frame, (w-160, by), (w-160+bw, by+15), bar_color, -1)
                    
                    # 绘制文字
                    frame = renderer.draw(frame, name.split(' ')[0], (w-220, by-5), (200,200,200), is_small=True)

        else:
            frame = renderer.draw(frame, "未检测到人脸", (50, 50), (0,0,255))

        cv2.imshow('Expression Expert V9 (Stable)', frame)
        
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'): break
        if key == ord('r'): 
            print("🔄 重新开始校准...")
            engine.calibrated = False
            engine.calib_frames = []

    detector.close()
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
