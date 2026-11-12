"""Actual FFmpeg lossless container and audio-only handling after HTTP transfer."""
import json,shutil,subprocess,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from download_engine import finish_media
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);source=root/'source.mkv'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=64x64:d=1','-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','mpeg4','-c:a','aac',str(source)],check=True)
    def packets(path,stream):
        return subprocess.check_output(['ffmpeg','-hide_banner','-loglevel','error','-i',str(path),'-map',stream,'-c','copy','-f','md5','-'],text=True).strip()
    video=packets(source,'0:v:0');audio=packets(source,'0:a:0')
    copy=root/'audio.mkv';shutil.copyfile(source,copy)
    finish_media(source,{'container':'mp4','quality':'best'})
    assert not source.exists() and packets(root/'source.mp4','0:v:0')==video
    assert packets(root/'source.mp4','0:a:0')==audio
    finish_media(copy,{'container':'mp4','quality':'audio'})
    assert not copy.exists() and packets(root/'audio.m4a','0:a:0')==audio
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(root/'audio.m4a')]))
    assert len(info['streams'])==1 and info['streams'][0]['codec_type']=='audio'
    print('PASS: FFmpeg remuxed original video/audio into MP4 and extracted M4A audio with identical encoded-packet hashes; no quality-reducing encode or residual video in the audio result.')
