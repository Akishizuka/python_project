import threading
import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw
import pygame
import sys
import ctypes
import random
import time
import os
import expressionlatest
import cv2 as cv2_mp 

#===================== Win32 API 常量与配置 ======================
user32 = ctypes.windll.user32
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = ctypes.c_bool

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
LWA_COLORKEY = 0x00000001

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

HWND_TOPMOST = ctypes.c_void_p(-1)
HWND_NOTOPMOST = ctypes.c_void_p(-2)
HWND_TOP = ctypes.c_void_p(0)

SW_HIDE = 0
SW_SHOWNA = 8

SPI_GETWORKAREA = 0x0030

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

def get_work_area():
    """返回 (left, top, right, bottom)，用于精确贴任务栏线"""
    rect = RECT()
    ok = user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    if ok:
        return rect.left, rect.top, rect.right, rect.bottom
    # 兜底：用全屏尺寸
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

def get_mouse_pos():
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

def get_hwnd():
    return pygame.display.get_wm_info()["window"]

def set_window_pos(hwnd, x, y):
    user32.SetWindowPos(hwnd, None, int(x), int(y), 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)

def set_window_size(hwnd, width, height):
    """设置窗口大小"""
    user32.SetWindowPos(hwnd, None, 0, 0, int(width), int(height), SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)

def set_window_topmost(hwnd, is_topmost):
    if is_topmost:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
    else:
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
    user32.RedrawWindow(hwnd, None, None, 0x0008 | 0x0010)

def hide_window(hwnd):
    user32.ShowWindow(hwnd, SW_HIDE)

def show_window(hwnd):
    user32.ShowWindow(hwnd, SW_SHOWNA)

#===================== 托盘功能 ======================
pet_visible = True
auto_move_enabled = False
topmost_enabled = False
running = True 

def create_tray_icon_image():
    img = Image.new("RGBA", (64, 64), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8,8,56,56), fill=(255,180,200,255))
    return img

def toggle_pet(icon, item):
    global pet_visible, hwnd
    pet_visible = not pet_visible
    if pet_visible:
        show_window(hwnd)
        if topmost_enabled:
            set_window_topmost(hwnd, True)
    else:
        hide_window(hwnd)

def toggle_auto_move(icon, item):
    global auto_move_enabled
    auto_move_enabled = not auto_move_enabled

def toggle_topmost(icon, item):
    global topmost_enabled, hwnd
    topmost_enabled = not topmost_enabled
    set_window_topmost(hwnd, topmost_enabled)
    icon.update_menu()

def quit_app(icon, item):
    global running
    running = False
    icon.stop()

def tray_thread():
    icon = pystray.Icon(
        "desktop_pet",
        create_tray_icon_image(),
        "桌宠",
        menu=pystray.Menu(
            item("显示/隐藏", toggle_pet),
            item(lambda text: f"自动移动 [{'开' if auto_move_enabled else '关'}]", toggle_auto_move),
            item(lambda text: f"窗口置顶 [{'开' if topmost_enabled else '关'}]", toggle_topmost),
            item("退出", quit_app)
        )
    )
    icon.run_detached()

#===================== 表情识别线程相关 ======================
latest_expression = "平静"
expression_lock = threading.Lock()

def expression_thread_func():
    """独立线程运行表情识别"""
    try:
        engine = expressionlatest.ExpressionExpertV9()
        renderer = expressionlatest.ChineseRenderer()
        
        model_path = 'face_landmarker.task'
        if not os.path.exists(model_path):
            print(f"模型文件缺失：{model_path}")
            print("请从 MediaPipe 官网下载 face_landmarker.task 并放入当前目录")
            return
        
        BaseOptions = expressionlatest.mp.tasks.BaseOptions
        FaceLandmarker = expressionlatest.mp.tasks.vision.FaceLandmarker
        FaceLandmarkerOptions = expressionlatest.mp.tasks.vision.FaceLandmarkerOptions
        VisionRunningMode = expressionlatest.mp.tasks.vision.RunningMode

        options = FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=VisionRunningMode.VIDEO,
            output_face_blendshapes=True,
            num_faces=1
        )
        detector = FaceLandmarker.create_from_options(options)
        
        cap = expressionlatest.cv2.VideoCapture(0)
        if not cap.isOpened():
            print("摄像头启动失败，请检查摄像头是否被占用")
            detector.close()
            return
        
        print("表情识别模块启动成功，按 Q 关闭摄像头窗口")
        print("表情识别线程：持续检测（每帧），桌宠端另有“读取间隔”节流")

        last_detection_time = 0
        last_result = "平静"
        last_color = (255, 255, 255)
        last_scores = {}
        last_calibrated = False

        while running:  
            success, frame = cap.read()
            if not success:
                print("摄像头读取失败，退出表情识别线程")
                break
            
            frame = expressionlatest.cv2.flip(frame, 1)
            h, w, _ = frame.shape
            
            current_time = time.time()

            mp_image = expressionlatest.mp.Image(
                image_format=expressionlatest.mp.ImageFormat.SRGB,
                data=expressionlatest.cv2.cvtColor(frame, expressionlatest.cv2.COLOR_BGR2RGB)
            )

            timestamp = int(current_time * 1000)
            res = detector.detect_for_video(mp_image, timestamp)

            if res.face_blendshapes:
                raw_dict = {b.category_name: b.score for b in res.face_blendshapes[0]}
                result, color, scores, calibrated = engine.analyze(raw_dict)
                
                if res.face_landmarks:
                    for lm in res.face_landmarks[0]:
                        px = int(lm.x * w)
                        py = int(lm.y * h)
                        cv2_mp.circle(frame, (px, py), 2, (0, 255, 0), -1, lineType=cv2_mp.LINE_AA)

                was_calibrating = not last_calibrated

                last_result = result
                last_color = color
                last_scores = scores
                last_calibrated = calibrated

                if calibrated and was_calibrating:
                    print("表情校准完成！开始持续检测")

                with expression_lock:
                    global latest_expression
                    latest_expression = result

                last_detection_time = current_time
            else:
                last_result = "未检测到人脸"
                last_color = (0, 0, 255)
                last_scores = {}
            
            if not last_calibrated:
                cv2_mp.rectangle(frame, (w//2-150, h//2-40), (w//2+150, h//2+40), (50,50,50), -1)
                calib_progress = len(engine.calib_frames)
                calib_text = f"校准中... ({calib_progress}/20)"
                frame = renderer.draw(frame, calib_text, (w//2-120, h//2-20), (0, 255, 0))
            else:
                cv2_mp.rectangle(frame, (0, h-80), (w, h), (30, 30, 30), -1)
                countdown_text = f"当前状态：{last_result} (持续检测)"
                frame = renderer.draw(frame, countdown_text, (30, h-65), last_color)
                
                if last_scores:
                    sorted_scores = sorted(last_scores.items(), key=lambda x: x[1], reverse=True)[:5]
                    for i, (name, val) in enumerate(sorted_scores):
                        by = 50 + i*40
                        cv2_mp.rectangle(frame, (w-160, by), (w-20, by+15), (50,50,50), -1)
                        bw = int(min(val, 2.0) / 2.0 * 140)
                        bar_color = last_color if name == last_result else (150,150,150)
                        cv2_mp.rectangle(frame, (w-160, by), (w-160+bw, by+15), bar_color, -1)
                        frame = renderer.draw(frame, name.split(' ')[0], (w-220, by-5), (200,200,200), is_small=True)

            cv2_mp.imshow('Expression Expert V9 (Stable)', frame)
            
            key = cv2_mp.waitKey(1) & 0xFF
            if key == ord('q'):
                print("手动关闭表情识别窗口")
                break
            if key == ord('r'):
                print("重新开始表情校准...")
                engine.calibrated = False
                engine.calib_frames = []
                last_calibrated = False

        detector.close()
        cap.release()
        cv2_mp.destroyAllWindows()
        print("表情识别模块已释放资源")
        
    except Exception as e:
        print(f"表情识别线程异常：{e}")
        try:
            cap.release()
            cv2_mp.destroyAllWindows()
        except:
            pass

# ===================== 初始化 Pygame ======================
pygame.init()
clock = pygame.time.Clock()

#人物实际大小（固定，作为默认值）
PET_SIZE = (150, 175)
#气泡区域大小
BUBBLE_AREA_WIDTH = 160  #右侧给气泡的空间
BUBBLE_AREA_HEIGHT = 35  #上方给气泡的空间

PROGRAM_FPS = 60
GIF_FPS = 60
FRAME_SWITCH = max(1, PROGRAM_FPS // GIF_FPS)  #每隔多少tick切一帧GIF

SCREEN_W = user32.GetSystemMetrics(0)
SCREEN_H = user32.GetSystemMetrics(1)

#初始窗口大小（会在加载GIF后动态调整）
#先使用默认大小，加载GIF后会重新设置
WINDOW_SIZE = (PET_SIZE[0] + BUBBLE_AREA_WIDTH, PET_SIZE[1] + BUBBLE_AREA_HEIGHT)
screen = pygame.display.set_mode(WINDOW_SIZE, pygame.NOFRAME | pygame.SRCALPHA)
pygame.display.set_caption("桌宠")

#启动托盘线程
threading.Thread(target=tray_thread, daemon=True).start()
#启动表情识别线程
threading.Thread(target=expression_thread_func, daemon=True).start()

hwnd = get_hwnd()
ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
user32.SetLayeredWindowAttributes(hwnd, 0x000000, 0, LWA_COLORKEY)
user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)

#===================== GIF 加载 ======================
GIF_SCALE_FACTOR = 0.5  #GIF缩放比例（50%）

def load_gif(path, size=None, keep_aspect=True):
    """
    加载GIF文件
    path: GIF文件路径
    size: 目标尺寸 (width, height)，如果为None则使用GIF原始尺寸的50%
    keep_aspect: 是否保持宽高比，如果True则按比例缩放并居中，如果False则强制拉伸到size
    返回: (frames, scaled_size) - frames是pygame surface列表，scaled_size是缩放后的尺寸
    """
    frames = []
    try:
        im = Image.open(path)
        original_size = im.size  #(width, height)
        
        if size is None:
            target_width = int(original_size[0] * GIF_SCALE_FACTOR)
            target_height = int(original_size[1] * GIF_SCALE_FACTOR)
            scaled_size = (target_width, target_height)
        else:
            target_width, target_height = size
            scaled_size = (target_width, target_height)
        
        for i in range(im.n_frames):
            im.seek(i)
            frame = im.convert("RGBA")
            
            if keep_aspect and size is not None:
                scale_w = target_width / original_size[0]
                scale_h = target_height / original_size[1]
                scale = max(scale_w, scale_h)  
                
                new_width = int(original_size[0] * scale)
                new_height = int(original_size[1] * scale)
                
                frame = frame.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                final_frame = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
                
                paste_x = (target_width - new_width) // 2
                paste_y = (target_height - new_height) // 2
                final_frame.paste(frame, (paste_x, paste_y), frame)
                
                frame = final_frame
            elif size is not None:
                frame = frame.resize((target_width, target_height), Image.Resampling.LANCZOS)
            else:
                frame = frame.resize((target_width, target_height), Image.Resampling.LANCZOS)
            
            surf = pygame.image.fromstring(frame.tobytes(), frame.size, "RGBA").convert_alpha()
            frames.append(surf)
    except Exception as e:
        print(f"GIF加载失败 {path} -> {e}")
        default_size = size if size else (int(PET_SIZE[0] * GIF_SCALE_FACTOR), int(PET_SIZE[1] * GIF_SCALE_FACTOR))
        surf = pygame.Surface(default_size, pygame.SRCALPHA)
        pygame.draw.rect(surf, (255,180,200,200), surf.get_rect(), border_radius=10)
        frames = [surf]
        scaled_size = default_size
    
    return frames, scaled_size

gifs = {
    "idle_left": r"resource\0\0.gif",
    "idle_right": r"resource\1\0.gif",
    "move_left": r"resource\0\move.gif",
    "move_right": r"resource\1\move.gif",
    #表情相关动画
    "happy_left": r"resource\0\1.gif",
    "happy_right": r"resource\1\1.gif",
    "sad_left": r"resource\0\skill1.gif",
    "sad_right": r"resource\1\skill1.gif",
    "angry_left": r"resource\0\attack.gif",
    "angry_right": r"resource\1\attack.gif",
    "surprised_left": r"resource\0\skill2loop.gif",
    "surprised_right": r"resource\1\skill2loop.gif",
    "skill_left": r"resource\0\skill1.gif",
    "skill_right": r"resource\1\skill1.gif",
}

#每个GIF在窗口中的水平偏移（用于对齐人物脚的位置，解决切换状态时左右抖动）
#正数：向右偏移；负数：向左偏移。可以根据实际资源微调。
gifs_offset_x = {
    "idle_left": 120,
    "idle_right": 50,
    "move_left": 0,
    "move_right": 0,
    "happy_left": 0,
    "happy_right": 50,
    "sad_left": 0,
    "sad_right": 0,
    "angry_left": 0,
    "angry_right": 0,
    "surprised_left": 0,
    "surprised_right": 0,
    "skill_left": 0,
    "skill_right": 0,
}

gifs_frames = {}
gifs_sizes = {}  
for state, path in gifs.items():
    frames, scaled_size = load_gif(path, size=None, keep_aspect=False)  
    gifs_frames[state] = frames
    gifs_sizes[state] = scaled_size

#===================== 初始化变量 ======================
pet_state = "idle"
facing_right = True
current_gif_frames = gifs_frames["idle_right"]
current_gif_size = gifs_sizes["idle_right"]  #当前GIF的尺寸
last_state_key = "idle_right"  #用于检测状态变化
current_offset_x = gifs_offset_x.get(last_state_key, 0)  #当前状态的水平偏移
expression_state = ""  #当前表情状态
expression_state_timer = 0  #表情状态持续时间
expression_state_duration = 180  #表情动作持续时间（帧数，约3秒）
#表情识别线程会持续检测；桌宠端只按间隔读取/响应（避免每帧都触发）
PET_EXPRESSION_READ_INTERVAL = 5.0
next_pet_expression_read_time = 0.0  #到该时间戳后才允许桌宠读取并响应一次表情

win_x = SCREEN_W - WINDOW_SIZE[0] - 50
win_y = 100
set_window_pos(hwnd, win_x, win_y)

dragging = False
offset_x = 0
offset_y = 0

#文字气泡相关
speech_bubble = ""
speech_bubble_timer = 0
speech_bubble_duration = 300  #显示时长（帧数，约5秒）
try:
    font = pygame.font.Font("C:/Windows/Fonts/msyh.ttc", 14)  
except:
    try:
        font = pygame.font.Font("C:/Windows/Fonts/simsun.ttc", 14)  
    except:
        font = pygame.font.Font(None, 20)  

#自动走动
last_switch_time = time.time()
switch_interval = random.uniform(5, 10)
move_step = 2
move_dir_x = random.choice([-1,1])

#重力
vel_y = 0
gravity = 0.5
elasticity = 0.1  

#人物在窗口中的固定位置
#上方留出气泡区域，但人物脚底仍贴在任务栏线上
PET_POS_X = 0
PET_POS_Y = BUBBLE_AREA_HEIGHT  

work_bottom = get_work_area()[3]
floor_y = work_bottom - (current_gif_size[1] + PET_POS_Y)

WINDOW_SIZE = (current_gif_size[0] + BUBBLE_AREA_WIDTH, current_gif_size[1] + BUBBLE_AREA_HEIGHT)
screen = pygame.display.set_mode(WINDOW_SIZE, pygame.NOFRAME | pygame.SRCALPHA)
set_window_size(hwnd, WINDOW_SIZE[0], WINDOW_SIZE[1])

#===================== 主循环 ======================
current_frame = 0
gif_timer = 0

while running:
    clock.tick(PROGRAM_FPS)
    screen.fill((0,0,0,0))
    mouse_x, mouse_y = get_mouse_pos()

    #事件处理
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            local_x, local_y = pygame.mouse.get_pos()
            #检查鼠标是否在人物区域内（
            pet_local_x = local_x - (PET_POS_X + current_offset_x)
            pet_local_y = local_y - PET_POS_Y
            if (0 <= pet_local_x < current_gif_size[0] and 0 <= pet_local_y < current_gif_size[1]):
                #确保坐标在GIF帧范围内
                frame_surf = current_gif_frames[current_frame]
                if pet_local_x < frame_surf.get_width() and pet_local_y < frame_surf.get_height():
                    pixel = frame_surf.get_at((pet_local_x, pet_local_y))
                    if pixel[3] > 0:
                        dragging = True
                        #offset 是鼠标相对于窗口的位置
                        offset_x = local_x
                        offset_y = local_y
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            dragging = False
            if topmost_enabled and pet_visible:
                set_window_topmost(hwnd, True)

    #拖动逻辑（拖动整个窗口）
    if dragging:
        win_x = mouse_x - offset_x
        win_y = mouse_y - offset_y
        win_x = max(0, min(win_x, SCREEN_W - WINDOW_SIZE[0]))
        max_win_y = get_work_area()[3] - (current_gif_size[1] + PET_POS_Y)
        win_y = max(0, min(win_y, max_win_y))

    with expression_lock:
        current_expr = latest_expression

    if not dragging and pet_visible:
        now_time = time.time()

        can_read_expression = (expression_state_timer <= 0) and (now_time >= next_pet_expression_read_time)

        triggered = False
        if can_read_expression:
            if "开心" in current_expr or "坏笑" in current_expr:
                pet_state = "happy"
                expression_state = "happy"
                key = f"happy_{'right' if facing_right else 'left'}"
                one_cycle_ticks = len(gifs_frames.get(key, [])) * FRAME_SWITCH
                expression_state_timer = one_cycle_ticks if one_cycle_ticks > 0 else expression_state_duration
                current_frame = 0
                gif_timer = 0
                speech_bubble = "看到你笑，我也变得好开心呀~ (๑>◡<๑)"
                speech_bubble_timer = speech_bubble_duration
                triggered = True

            elif "难过" in current_expr or "委屈" in current_expr:
                pet_state = "sad"
                expression_state = "sad"
                key = f"sad_{'right' if facing_right else 'left'}"
                one_cycle_ticks = len(gifs_frames.get(key, [])) * FRAME_SWITCH
                expression_state_timer = one_cycle_ticks if one_cycle_ticks > 0 else expression_state_duration
                current_frame = 0
                gif_timer = 0
                speech_bubble = "别难过啦，我会一直陪着你的。抱抱~ "
                speech_bubble_timer = speech_bubble_duration
                triggered = True

            elif "愤怒" in current_expr or "傲娇" in current_expr:
                pet_state = "angry"
                expression_state = "angry"
                key = f"angry_{'right' if facing_right else 'left'}"
                one_cycle_ticks = len(gifs_frames.get(key, [])) * FRAME_SWITCH
                expression_state_timer = one_cycle_ticks if one_cycle_ticks > 0 else expression_state_duration
                current_frame = 0
                gif_timer = 0
                speech_bubble = "哎呀，谁惹你生气了？别气别气，气坏身体不划算！"
                speech_bubble_timer = speech_bubble_duration
                triggered = True

            elif "惊讶" in current_expr or "发呆" in current_expr:
                pet_state = "surprised"
                expression_state = "surprised"
                key = f"surprised_{'right' if facing_right else 'left'}"
                one_cycle_ticks = len(gifs_frames.get(key, [])) * FRAME_SWITCH
                expression_state_timer = one_cycle_ticks if one_cycle_ticks > 0 else expression_state_duration
                current_frame = 0
                gif_timer = 0
                speech_bubble = "嘿！你看到什么了这么惊讶，我也要看看！"
                speech_bubble_timer = speech_bubble_duration
                triggered = True

            elif "平静" in current_expr:
                triggered = False

            next_pet_expression_read_time = now_time + PET_EXPRESSION_READ_INTERVAL

        if (not triggered) and (expression_state_timer <= 0):
            if auto_move_enabled:
                if now_time - last_switch_time >= switch_interval:
                    pet_state = "move" if random.random() < 0.7 else "idle"
                    move_dir_x = random.choice([-1, 1])
                    facing_right = move_dir_x > 0
                    last_switch_time = now_time
                    switch_interval = random.uniform(5, 10)

                if pet_state == "move":
                    win_x += move_dir_x * move_step
            else:
                pet_state = "idle"

    if expression_state_timer > 0:
        expression_state_timer -= 1
        if expression_state_timer == 0 and expression_state:
            expression_state = ""
            if pet_state not in ["move"]:
                pet_state = "idle"
            next_pet_expression_read_time = time.time() + PET_EXPRESSION_READ_INTERVAL

    if pet_state == "move":
        win_x += move_dir_x * move_step
        if win_x <= 0 or win_x >= SCREEN_W - current_gif_size[0]:
            move_dir_x *= -1
            facing_right = move_dir_x > 0
            win_x = max(0, min(win_x, SCREEN_W - current_gif_size[0]))

    state_key = f"{pet_state}_{'right' if facing_right else 'left'}"
    if state_key not in gifs_frames:
        state_key = f"idle_{'right' if facing_right else 'left'}"
    
    if state_key != last_state_key:
        current_gif_size = gifs_sizes[state_key]
        new_window_width = current_gif_size[0] + BUBBLE_AREA_WIDTH
        new_window_height = current_gif_size[1] + BUBBLE_AREA_HEIGHT
        
        if (new_window_width, new_window_height) != WINDOW_SIZE:
            WINDOW_SIZE = (new_window_width, new_window_height)
            screen = pygame.display.set_mode(WINDOW_SIZE, pygame.NOFRAME | pygame.SRCALPHA)
            set_window_size(hwnd, WINDOW_SIZE[0], WINDOW_SIZE[1])
            floor_y = get_work_area()[3] - (current_gif_size[1] + PET_POS_Y)
        
        floor_y = get_work_area()[3] - (current_gif_size[1] + PET_POS_Y)
        if not dragging:
            win_y = floor_y
            vel_y = 0

        current_offset_x = gifs_offset_x.get(state_key, 0)

        last_state_key = state_key
    
    current_gif_frames = gifs_frames[state_key]

    vel_y += gravity
    win_y += vel_y
    floor_y = get_work_area()[3] - (current_gif_size[1] + PET_POS_Y)
    if win_y >= floor_y:
        win_y = floor_y
        vel_y = 0

    if pet_visible:
        set_window_pos(hwnd, win_x, win_y)

    gif_timer += 1
    if gif_timer >= FRAME_SWITCH:
        current_frame = (current_frame + 1) % len(current_gif_frames)
        gif_timer = 0

    screen.blit(current_gif_frames[current_frame], (PET_POS_X + current_offset_x, PET_POS_Y))
    
    if speech_bubble and speech_bubble_timer > 0:
        speech_bubble_timer -= 1
        
        words = speech_bubble.split('\n')
        max_width = 180
        lines = []
        for word_line in words:
            if font.size(word_line)[0] > max_width:
                chars = list(word_line)
                current_line = ""
                for char in chars:
                    test_line = current_line + char
                    if font.size(test_line)[0] > max_width:
                        if current_line:
                            lines.append(current_line)
                        current_line = char
                    else:
                        current_line = test_line
                if current_line:
                    lines.append(current_line)
            else:
                lines.append(word_line)
        
        line_height = 22
        padding_x = 14
        padding_y = 10
        bubble_width = max([font.size(line)[0] for line in lines]) + padding_x * 2
        bubble_height = len(lines) * line_height + padding_y * 2
        
        shadow_offset = 3
        arrow_size = 10
        total_width = bubble_width + shadow_offset * 2 + arrow_size  
        total_height = bubble_height + shadow_offset * 2
        
        bubble_x = WINDOW_SIZE[0] - bubble_width - 8  
        bubble_y = 8  
        
        bubble_x = max(arrow_size, min(bubble_x, WINDOW_SIZE[0] - bubble_width))
        bubble_y = max(0, min(bubble_y, WINDOW_SIZE[1] - bubble_height))
        
        bubble_surf = pygame.Surface((total_width, total_height), pygame.SRCALPHA)
        
        bubble_offset_x = arrow_size
        
        bubble_bg_color = (255, 255, 255, 255)  
        bubble_border_color = (200, 200, 200, 255)  
        
        pygame.draw.rect(bubble_surf, bubble_bg_color, 
                        (bubble_offset_x, 0, bubble_width, bubble_height), border_radius=16)
        
        pygame.draw.rect(bubble_surf, bubble_border_color, 
                        (bubble_offset_x, 0, bubble_width, bubble_height), width=1, border_radius=16)
        
        arrow_y = bubble_height - arrow_size - 10
        arrow_points = [
            (bubble_offset_x, arrow_y - arrow_size // 2),  
            (bubble_offset_x, arrow_y + arrow_size // 2),  
            (arrow_size // 3, arrow_y)  
        ]
        
        pygame.draw.polygon(bubble_surf, bubble_bg_color, arrow_points)
        pygame.draw.polygon(bubble_surf, bubble_border_color, arrow_points, width=1)
        
        text_color = (50, 50, 50, 255)
        for i, line in enumerate(lines):
            text_surf = font.render(line, True, text_color)
            bubble_surf.blit(text_surf, (bubble_offset_x + padding_x, padding_y + i * line_height))
        
        screen.blit(bubble_surf, (bubble_x - arrow_size, bubble_y))
        
    elif speech_bubble_timer <= 0:
        # 时间到了，清空气泡
        speech_bubble = ""
    
    pygame.display.flip()

# 程序退出清理
pygame.quit()
sys.exit()