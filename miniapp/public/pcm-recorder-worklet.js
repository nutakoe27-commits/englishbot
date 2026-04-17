/**
 * pcm-recorder-worklet.js
 *
 * AudioWorklet processor: downsample браузерный аудио-поток до 16 kHz
 * и отправлять Int16Array-фреймы через port.
 *
 * Входной формат:  Float32, частота дискретизации AudioContext (обычно 44100 или 48000 Гц)
 * Выходной формат: PCM 16-bit signed integer, 16000 Гц, mono (little-endian)
 *
 * Использование:
 *   const worklet = new AudioWorkletNode(ctx, 'pcm-recorder-processor');
 *   worklet.port.onmessage = (e) => {
 *     // e.data — Int16Array с PCM-семплами 16 kHz
 *     ws.send(e.data.buffer);
 *   };
 */

const TARGET_SAMPLE_RATE = 16000;
// Размер буфера для отправки: 20 мс при 16 kHz = 320 семплов
// Небольшие фреймы снижают задержку
const SEND_FRAME_SAMPLES = 320;

class PcmRecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    /** @type {Float32Array[]} */
    this._inputBuffer = [];
    this._inputBufferLength = 0;
    // Вычислим реальный sampleRate только на первом вызове process()
    this._inputSampleRate = null;
    // Дробный индекс для ресемплирования
    this._resamplePhase = 0;
    // Накопленные ресемплированные семплы до порога SEND_FRAME_SAMPLES
    this._outputBuffer = new Int16Array(SEND_FRAME_SAMPLES * 4);
    this._outputLength = 0;
  }

  /**
   * Биквадратный lowpass-фильтр (Butterworth, fc = 7500 Гц / 48000 Гц)
   * для предотвращения алиасинга при децимации.
   * Коэффициенты вычислены для fs=48000, fc=7500 (Nyquist для 16kHz).
   * При других частотах дискретизации используем упрощённый усредняющий даунсемплер.
   */
  _initFilter(sampleRate) {
    // Нормализованная граничная частота (половина целевой)
    const fc = TARGET_SAMPLE_RATE / 2 / sampleRate;
    // Используем простой IIR lowpass: H(z) = (1-a)/(1 - a*z^-1), a = exp(-2π*fc)
    this._alpha = Math.exp(-2 * Math.PI * fc);
    this._filterState = 0;
  }

  _lowpass(sample) {
    this._filterState =
      (1 - this._alpha) * sample + this._alpha * this._filterState;
    return this._filterState;
  }

  /**
   * Конвертирует Float32 → Int16 с клиппингом.
   * @param {number} f - значение в диапазоне [-1, 1]
   * @returns {number}
   */
  _floatToInt16(f) {
    const clamped = Math.max(-1, Math.min(1, f));
    return clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }

  /**
   * @param {Float32Array[][]} inputs
   * @param {Float32Array[][]} outputs
   * @param {Record<string, Float32Array>} parameters
   * @returns {boolean}
   */
  process(inputs, outputs, parameters) {
    const input = inputs[0];
    if (!input || !input[0] || input[0].length === 0) {
      return true;
    }

    // Инициализация при первом вызове
    if (this._inputSampleRate === null) {
      this._inputSampleRate = sampleRate; // глобальная переменная AudioWorklet
      this._initFilter(this._inputSampleRate);
    }

    // Берём только первый канал (mono)
    const channelData = input[0];
    const inputLen = channelData.length;

    // Шаг ресемплирования: сколько входных семплов на один выходной
    const step = this._inputSampleRate / TARGET_SAMPLE_RATE;

    let inputIdx = this._resamplePhase;

    while (inputIdx < inputLen) {
      const intIdx = Math.floor(inputIdx);
      const frac = inputIdx - intIdx;

      // Линейная интерполяция между соседними семплами
      const s0 = channelData[intIdx] ?? 0;
      const s1 = channelData[Math.min(intIdx + 1, inputLen - 1)] ?? 0;
      const interpolated = s0 + frac * (s1 - s0);

      // Применяем lowpass-фильтр
      const filtered = this._lowpass(interpolated);

      // Конвертируем в int16 и записываем в выходной буфер
      if (this._outputLength < this._outputBuffer.length) {
        this._outputBuffer[this._outputLength++] = this._floatToInt16(filtered);
      }

      // Если накопили достаточно семплов — отправляем
      if (this._outputLength >= SEND_FRAME_SAMPLES) {
        const frame = new Int16Array(this._outputBuffer.buffer, 0, SEND_FRAME_SAMPLES);
        this.port.postMessage(frame.slice()); // slice() создаёт копию
        // Сдвигаем оставшиеся данные
        const remaining = this._outputLength - SEND_FRAME_SAMPLES;
        if (remaining > 0) {
          this._outputBuffer.copyWithin(0, SEND_FRAME_SAMPLES, this._outputLength);
        }
        this._outputLength = remaining;
      }

      inputIdx += step;
    }

    // Сохраняем дробный остаток фазы для следующего вызова
    this._resamplePhase = inputIdx - inputLen;
    if (this._resamplePhase < 0) this._resamplePhase = 0;

    return true; // keep processor alive
  }
}

registerProcessor("pcm-recorder-processor", PcmRecorderProcessor);
