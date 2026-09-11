// IM 语音条 MediaRecorder 麦克风约束：单声道 + AGC/EC/NS 全开。
// 不写 sampleRate：MediaRecorder/webm-opus 由浏览器按设备原生采样率编码，强制 16 kHz
// 在不支持该采样率的麦克风驱动上会抛 OverconstrainedError。
export const IM_VOICE_BAR_AUDIO_CONSTRAINTS: MediaTrackConstraints = {
  autoGainControl: true,
  channelCount: 1,
  echoCancellation: true,
  noiseSuppression: true
}
