#!/usr/bin/env python3

import sys, os, ctypes, ctypes.util, logging, getopt, signal, atexit, time
from cameractrls import CameraCtrls, PTZController, find_usb_ids_in_sysfs, get_configfilename
import cameraptzc60e

sdl2lib = ctypes.util.find_library('SDL2-2.0')
if sdl2lib is None:
    logging.error('libSDL2 not found, please install the libsdl2-2.0 package!')
    sys.exit(2)
sdl2 = ctypes.CDLL(sdl2lib)

#logging.getLogger().setLevel(logging.INFO)

enum = ctypes.c_uint

SDL_Init = sdl2.SDL_Init
SDL_Init.restype = ctypes.c_int
SDL_Init.argtypes = [ctypes.c_uint32]
# int SDL_Init(Uint32 flags);

SDL_GetError = sdl2.SDL_GetError
SDL_GetError.restype = ctypes.c_char_p
SDL_GetError.argtypes = []
# const char* SDL_GetError(void);

SDL_NumJoysticks = sdl2.SDL_NumJoysticks
SDL_NumJoysticks.restype = ctypes.c_int
# int SDL_NumJoysticks(void)

SDL_IsGameController = sdl2.SDL_IsGameController
SDL_IsGameController.restype = ctypes.c_bool
SDL_IsGameController.argtypes = [ctypes.c_int]
# SDL_bool SDL_IsGameController(int joystick_index);

SDL_GameControllerNameForIndex = sdl2.SDL_GameControllerNameForIndex
SDL_GameControllerNameForIndex.restype = ctypes.c_char_p
SDL_GameControllerNameForIndex.argtypes = [ctypes.c_int]
# const char* SDL_GameControllerNameForIndex(int joystick_index);

SDL_JoystickID = ctypes.c_int32

SDL_GameControllerOpen = sdl2.SDL_GameControllerOpen
SDL_GameControllerOpen.restype = ctypes.c_void_p
SDL_GameControllerOpen.argtypes = [ctypes.c_int]
# SDL_GameController* SDL_GameControllerOpen(int joystick_index);

SDL_GameControllerRumble = sdl2.SDL_GameControllerRumble
SDL_GameControllerRumble.restype = ctypes.c_int
SDL_GameControllerRumble.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32]
# int SDL_GameControllerRumble(SDL_GameController *gamecontroller, Uint16 low_frequency_rumble, Uint16 high_frequency_rumble, Uint32 duration_ms);

SDL_GameControllerAxis = enum
SDL_CONTROLLER_AXIS_LEFTX = 0
SDL_CONTROLLER_AXIS_LEFTY = 1
SDL_CONTROLLER_AXIS_RIGHTX = 2
SDL_CONTROLLER_AXIS_RIGHTY = 3
SDL_CONTROLLER_AXIS_TRIGGERLEFT = 4
SDL_CONTROLLER_AXIS_TRIGGERRIGHT = 5

SDL_GameControllerButton = enum
SDL_CONTROLLER_BUTTON_A = 0
SDL_CONTROLLER_BUTTON_B = 1
SDL_CONTROLLER_BUTTON_X = 2
SDL_CONTROLLER_BUTTON_Y = 3
SDL_CONTROLLER_BUTTON_BACK = 4
SDL_CONTROLLER_BUTTON_GUIDE = 5
SDL_CONTROLLER_BUTTON_START = 6
SDL_CONTROLLER_BUTTON_LEFTSTICK = 7
SDL_CONTROLLER_BUTTON_RIGHTSTICK = 8
SDL_CONTROLLER_BUTTON_LEFTSHOULDER = 9
SDL_CONTROLLER_BUTTON_RIGHTSHOULDER = 10
SDL_CONTROLLER_BUTTON_DPAD_UP = 11
SDL_CONTROLLER_BUTTON_DPAD_DOWN = 12
SDL_CONTROLLER_BUTTON_DPAD_LEFT = 13
SDL_CONTROLLER_BUTTON_DPAD_RIGHT = 14

SDL_GameControllerGetAxis = sdl2.SDL_GameControllerGetAxis
SDL_GameControllerGetAxis.restype = ctypes.c_int16
SDL_GameControllerGetAxis.argtypes = [ctypes.c_void_p, SDL_GameControllerAxis]
# Sint16 SDL_GameControllerGetAxis(SDL_GameController *gamecontroller, SDL_GameControllerAxis axis);

SDL_GameControllerGetButton = sdl2.SDL_GameControllerGetButton
SDL_GameControllerGetButton.restype = ctypes.c_int8
SDL_GameControllerGetButton.argtypes = [ctypes.c_void_p, SDL_GameControllerButton]
# Uint8 SDL_GameControllerGetButton(SDL_GameController *gamecontroller, SDL_GameControllerButton button);

SDL_GameControllerGetAttached = sdl2.SDL_GameControllerGetAttached
SDL_GameControllerGetAttached.restype = ctypes.c_bool
SDL_GameControllerGetAttached.argtypes = [ctypes.c_void_p]
# SDL_bool SDL_GameControllerGetAttached(SDL_GameController *gamecontroller);

SDL_GameControllerClose = sdl2.SDL_GameControllerClose
SDL_GameControllerClose.argtypes = [ctypes.c_void_p]
# void SDL_GameControllerClose(SDL_GameController *gamecontroller);

SDL_PollEvent = sdl2.SDL_PollEvent
SDL_PollEvent.restype = ctypes.c_int
SDL_PollEvent.argtypes = [ctypes.c_void_p]
# int SDL_PollEvent(SDL_Event * event);

SDL_Quit = sdl2.SDL_Quit
# void SDL_Quit(void);

SDL_INIT_GAMECONTROLLER = 0x00002000
SDL_INIT_EVENTS = 0x00004000

SDL_QUIT = 0x100
SDL_CONTROLLERDEVICEADDED = 0x653
SDL_CONTROLLERDEVICEREMOVED = 0x654

_event_pad_size = 56 if ctypes.sizeof(ctypes.c_void_p) <= 8 else 64

class SDL_ControllerDeviceEvent(ctypes.Structure):
    _fields_ = [
        ('type', ctypes.c_uint32),
        ('timestamp', ctypes.c_uint32),
        ('which', SDL_JoystickID),
    ]

class SDL_Event(ctypes.Union):
    _fields_ = [
        ('type', ctypes.c_uint32),
        ('cdevice', SDL_ControllerDeviceEvent),
        ('padding', (ctypes.c_uint8 * _event_pad_size)),
    ]

SDL_QuitEvent = SDL_Event()
SDL_QuitEvent.type = SDL_QUIT

SDL_PushEvent = sdl2.SDL_PushEvent
SDL_PushEvent.restype = ctypes.c_int
SDL_PushEvent.argtypes = [ctypes.POINTER(SDL_Event)]
#int SDL_PushEvent(SDL_Event * event);

def usage():
    print(f'usage: {sys.argv[0]} [-h] [-l] [-m] [-c] [-d DEVICE]\n')
    print(f'optional arguments:')
    print(f'  -h, --help        show this help message and exit')
    print(f'  -l, --list        list gamecontrollers')
    print(f'  -m, --map         print the EMEET C60E button map for DEVICE and exit')
    print(f'  -c, --controller  use controller id (0..n), default first')
    print(f'  -d DEVICE         use DEVICE, default /dev/video0')
    print()
    print(f'example:')
    print(f'  {sys.argv[0]} -d /dev/video4')

th = 4096

def rumble_simple(controller):
    SDL_GameControllerRumble(controller, 0x0a00, 0x0aff, 50)

def check_zoom(controller, axis, cb, scale=1, rumble=rumble_simple):
    value = SDL_GameControllerGetAxis(controller, axis)
    if value == 0:
        return 0
    if cb(round(value / 2048) * scale, []):
        rumble(controller)

def check_axis(controller, axis, cb, scale=1, rumble=rumble_simple):
    value = SDL_GameControllerGetAxis(controller, axis)
    if -th < value < th:
        return cb(0, [])
    if cb(round(value / 8192) * scale, []):
        rumble(controller)

def check_axis_abs(controller, axis, cb, scale=1, rumble=rumble_simple):
    value = SDL_GameControllerGetAxis(controller, axis)
    if -th < value < th:
        return 0
    if cb(round(value / 8192) * scale, []):
        rumble(controller)

def check_button_v(controller, button, cb, scale=1, rumble=rumble_simple):
    value = SDL_GameControllerGetButton(controller, button)
    if value == 0:
        return 0
    if cb(value * scale, []):
        rumble(controller)

def check_button(controller, button, cb, rumble=rumble_simple):
    value = SDL_GameControllerGetButton(controller, button)
    if value == 0:
        return 0
    if cb([]):
        rumble(controller)

PAD_BUTTONS = {
    'a': SDL_CONTROLLER_BUTTON_A, 'b': SDL_CONTROLLER_BUTTON_B,
    'x': SDL_CONTROLLER_BUTTON_X, 'y': SDL_CONTROLLER_BUTTON_Y,
    'back': SDL_CONTROLLER_BUTTON_BACK, 'guide': SDL_CONTROLLER_BUTTON_GUIDE,
    'start': SDL_CONTROLLER_BUTTON_START,
    'leftstick': SDL_CONTROLLER_BUTTON_LEFTSTICK, 'rightstick': SDL_CONTROLLER_BUTTON_RIGHTSTICK,
    'leftshoulder': SDL_CONTROLLER_BUTTON_LEFTSHOULDER, 'rightshoulder': SDL_CONTROLLER_BUTTON_RIGHTSHOULDER,
    'dpup': SDL_CONTROLLER_BUTTON_DPAD_UP, 'dpdown': SDL_CONTROLLER_BUTTON_DPAD_DOWN,
    'dpleft': SDL_CONTROLLER_BUTTON_DPAD_LEFT, 'dpright': SDL_CONTROLLER_BUTTON_DPAD_RIGHT,
}

PAD_AXES = {
    'leftx': SDL_CONTROLLER_AXIS_LEFTX, 'lefty': SDL_CONTROLLER_AXIS_LEFTY,
    'rightx': SDL_CONTROLLER_AXIS_RIGHTX, 'righty': SDL_CONTROLLER_AXIS_RIGHTY,
    'lefttrigger': SDL_CONTROLLER_AXIS_TRIGGERLEFT, 'righttrigger': SDL_CONTROLLER_AXIS_TRIGGERRIGHT,
}

def read_pad_state(controller):
    buttons = {n for n, b in PAD_BUTTONS.items() if SDL_GameControllerGetButton(controller, b)}
    axes = {n: SDL_GameControllerGetAxis(controller, a) for n, a in PAD_AXES.items()}
    return buttons, axes

stop_requested = False

def request_stop(signum, frame):
    global stop_requested
    stop_requested = True
    SDL_PushEvent(SDL_QuitEvent)

def say(msg):
    print(msg, flush=True)

def main():
    try:
        arguments, values = getopt.getopt(sys.argv[1:], 'hlmc:d:', ['help', 'list', 'map', 'controller', 'device'])
    except getopt.error as err:
        print(err)
        usage()
        sys.exit(2)

    list_ctrls = False
    show_map = False
    controller_id = None
    device = '/dev/video0'

    controller = None
    ptz = None

    controllers = []

    for current_argument, current_value in arguments:
        if current_argument in ('-h', '--help'):
            usage()
            sys.exit(0)
        elif current_argument in ('-l', '--list'):
            list_ctrls = True
        elif current_argument in ('-m', '--map'):
            show_map = True
        elif current_argument in ('-c', '--controller'):
            controller_id = int(current_value.split(':')[-1])
        elif current_argument in ('-d', '--device'):
            device = current_value

    if show_map:
        profile = cameraptzc60e.load_profile(cameraptzc60e.read_ini(get_configfilename(device)))
        print(cameraptzc60e.format_map(*profile))
        sys.exit(0)

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    if SDL_Init(SDL_INIT_GAMECONTROLLER | SDL_INIT_EVENTS) != 0:
        logging.error(f'SDL_Init failed: {SDL_GetError()}')
        sys.exit(1)

    atexit.register(SDL_Quit)

    for i in range(SDL_NumJoysticks()):
        if SDL_IsGameController(i):
            controllers.append((SDL_GameControllerNameForIndex(i).decode(), i))

    if list_ctrls:
        for c in controllers:
            print(f'{c[0]}:{c[1]}')
        sys.exit(0)

    if controller_id is None and controllers:
        controller_id = controllers[0][1]

    if controller_id is None:
        logging.warning(f'controller not found, waiting to appear one..')

    if controller_id is not None and not SDL_IsGameController(controller_id):
        logging.error(f'controller with id "{controller_id}" is not a game controller')
        sys.exit(1)

    try:
        fd = os.open(device, os.O_RDWR, 0)
    except Exception as e:
        logging.error(f'os.open({device}, os.O_RDWR, 0) failed: {e}')
        sys.exit(2)

    camera_ctrls = CameraCtrls(device, fd)
    if not camera_ctrls.has_ptz():
        logging.error(f'camera {device} cannot do PTZ')
        sys.exit(1)

    ptz = PTZController(camera_ctrls)

    pad = None
    viewer = None
    listener = None
    if find_usb_ids_in_sysfs(device) == cameraptzc60e.C60E_USB_ID:
        config = cameraptzc60e.read_ini(get_configfilename(device))
        viewer = cameraptzc60e.ViewerLauncher(device)
        pad = cameraptzc60e.C60EPad(
            camera_ctrls, config,
            lambda num, errs: camera_ctrls.config_preset.load_preset(device, num, errs),
            viewer.spawn, viewer.toggle_hint, say)
        say('EMEET C60E pad map:')
        say(cameraptzc60e.format_map(pad.bindings, pad.settings, pad.steps))
        listener = camera_ctrls.subscribe_events(lambda c: None, lambda errs: say(f'warning: {errs}'))

    try:
        running = True
        event = SDL_Event()
        while running and not stop_requested:
            while SDL_PollEvent(ctypes.byref(event)):
                if event.type == SDL_QUIT:
                    running = False
                if event.type == SDL_CONTROLLERDEVICEREMOVED and controller is not None and not SDL_GameControllerGetAttached(controller):
                    say('pad unplugged, exiting')
                    running = False
                if event.type == SDL_CONTROLLERDEVICEADDED:
                    if controller_id is None:
                        controller_id = event.cdevice.which
                    if event.cdevice.which == controller_id:
                        if controller is not None:
                            SDL_GameControllerClose(controller)
                            controller = None
                        controller = SDL_GameControllerOpen(controller_id)
                        if controller is None:
                            logging.error(f'SDL_GameControllerOpen failed: {SDL_GetError()}')
                            sys.exit(1)

                        SDL_GameControllerRumble(controller, 0x00ff, 0xffff, 250)

            if pad is not None:
                if controller is not None and running:
                    buttons, axes = read_pad_state(controller)
                    pad.update(time.monotonic(), buttons, axes)
                time.sleep(0.020)
                continue

            if ptz.has_pantilt_speed:
                check_axis(controller, SDL_CONTROLLER_AXIS_LEFTX, ptz.do_pan_speed)
                check_axis(controller, SDL_CONTROLLER_AXIS_LEFTY, ptz.do_tilt_speed, -1)
            elif ptz.has_pantilt_absolute:
                check_axis(controller, SDL_CONTROLLER_AXIS_LEFTX, ptz.do_pan_step)
                check_axis(controller, SDL_CONTROLLER_AXIS_LEFTY, ptz.do_tilt_step, -1)

            check_axis_abs(controller, SDL_CONTROLLER_AXIS_RIGHTX, ptz.do_pan_step)
            check_axis_abs(controller, SDL_CONTROLLER_AXIS_RIGHTY, ptz.do_tilt_step, -1)

            check_zoom(controller, SDL_CONTROLLER_AXIS_TRIGGERLEFT, ptz.do_zoom_step, -1)
            check_zoom(controller, SDL_CONTROLLER_AXIS_TRIGGERRIGHT, ptz.do_zoom_step)

            check_button_v(controller, SDL_CONTROLLER_BUTTON_DPAD_LEFT, ptz.do_pan_step, -1)
            check_button_v(controller, SDL_CONTROLLER_BUTTON_DPAD_RIGHT, ptz.do_pan_step)
            check_button_v(controller, SDL_CONTROLLER_BUTTON_DPAD_UP, ptz.do_tilt_step)
            check_button_v(controller, SDL_CONTROLLER_BUTTON_DPAD_DOWN, ptz.do_tilt_step, -1)

            check_button(controller, SDL_CONTROLLER_BUTTON_GUIDE, ptz.do_reset)

            check_button_v(controller, SDL_CONTROLLER_BUTTON_A, ptz.do_preset, 1)
            check_button_v(controller, SDL_CONTROLLER_BUTTON_B, ptz.do_preset, 2)
            check_button_v(controller, SDL_CONTROLLER_BUTTON_X, ptz.do_preset, 3)
            check_button_v(controller, SDL_CONTROLLER_BUTTON_Y, ptz.do_preset, 4)
            check_button_v(controller, SDL_CONTROLLER_BUTTON_LEFTSHOULDER, ptz.do_preset, 5)
            check_button_v(controller, SDL_CONTROLLER_BUTTON_RIGHTSHOULDER, ptz.do_preset, 6)
            check_button_v(controller, SDL_CONTROLLER_BUTTON_BACK, ptz.do_preset, 7)
            check_button_v(controller, SDL_CONTROLLER_BUTTON_START, ptz.do_preset, 8)

            time.sleep(0.050)
    finally:
        if listener is not None:
            listener.stop()

    if viewer is not None:
        viewer.close()
    if controller is not None:
        SDL_GameControllerClose(controller)

if __name__ == '__main__':
    sys.exit(main())
