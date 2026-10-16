import os, re, subprocess, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import cameractrls as m

dev = sys.argv[1]


def get(name):
    out = subprocess.run(['v4l2-ctl', '-d', dev, f'--get-ctrl={name}'], capture_output=True, text=True, check=True).stdout
    return int(re.search(r':\s*(-?\d+)', out).group(1))


def put(name, value):
    subprocess.run(['v4l2-ctl', '-d', dev, f'--set-ctrl={name}={value}'], capture_output=True, text=True)


def write_verified(want):
    return m.write_verified(lambda v: put('auto_exposure', v), lambda: (time.sleep(0.2), get('auto_exposure'))[1], want)


mode, exposure = get('auto_exposure'), get('exposure_time_absolute')
target = 3 if mode == 1 else 1
idle = subprocess.run(['fuser', dev], capture_output=True, text=True).stdout.strip() == ''
result = {}
try:
    if idle:
        put('auto_exposure', target)
        time.sleep(0.3)
        result['IDLE_UNCHANGED'] = int(get('auto_exposure') == mode)
        with m.ShortStream(dev) as st:
            result['STREAMING'] = int(st.streaming)
            result['STREAM_CHANGED'] = int(write_verified(target))
    else:
        result['IDLE_UNCHANGED'] = 'skipped, something else streams'
        with m.ShortStream(dev) as st:
            result['STREAM_CHANGED'] = int(write_verified(target))
finally:
    with m.ShortStream(dev):
        write_verified(mode)
        if mode == 1:
            put('exposure_time_absolute', exposure)
    result['RESTORED'] = int(get('auto_exposure') == mode and (mode != 1 or get('exposure_time_absolute') == exposure))
for k, v in result.items():
    print(f'{k}={v}')
