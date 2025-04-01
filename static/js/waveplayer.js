class WavePlayer {
  constructor(container, options = {}) {
    this.container = container;
    this.options = {
      waveColor: '#d1d6e0',
      progressColor: '#5046e5',
      cursorColor: '#5046e5',
      cursorWidth: 2,
      height: 80,
      responsive: true,
      barWidth: 2,
      barGap: 1,
      hideScrollbar: true,
      ...options
    };
    
    this.isPlaying = false;
    this.wavesurfer = null;
    this.loadingIndicator = null;
    this.playButton = null;
    
    this.init();
  }
  
  init() {
    // Create player UI
    this.buildUI();
    
    // Initialize wavesurfer
    this.initWavesurfer();
    
    // Setup event listeners
    this.setupEvents();
  }
  
  buildUI() {
    // Clear container
    this.container.innerHTML = '';
    this.container.classList.add('waveplayer');
    
    // Create elements
    const waveformContainer = document.createElement('div');
    waveformContainer.className = 'waveplayer-waveform';
    
    const controlsContainer = document.createElement('div');
    controlsContainer.className = 'waveplayer-controls';
    
    // Play button
    this.playButton = document.createElement('button');
    this.playButton.className = 'waveplayer-play-btn';
    this.playButton.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="play-icon">
        <polygon points="5 3 19 12 5 21 5 3"></polygon>
      </svg>
      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="pause-icon" style="display: none;">
        <rect x="6" y="4" width="4" height="16"></rect>
        <rect x="14" y="4" width="4" height="16"></rect>
      </svg>
    `;
    
    // Time display
    this.timeDisplay = document.createElement('div');
    this.timeDisplay.className = 'waveplayer-time';
    this.timeDisplay.textContent = '0:00 / 0:00';
    
    // Loading indicator
    this.loadingIndicator = document.createElement('div');
    this.loadingIndicator.className = 'waveplayer-loading';
    this.loadingIndicator.innerHTML = `
      <div class="waveplayer-spinner"></div>
      <span>Loading...</span>
    `;
    
    // Append elements
    controlsContainer.appendChild(this.playButton);
    controlsContainer.appendChild(this.timeDisplay);
    
    this.container.appendChild(controlsContainer);
    this.container.appendChild(waveformContainer);
    this.container.appendChild(this.loadingIndicator);
    
    // Store reference to waveform container
    this.waveformContainer = waveformContainer;
  }
  
  initWavesurfer() {
    // Initialize WaveSurfer
    this.wavesurfer = WaveSurfer.create({
      container: this.waveformContainer,
      ...this.options
    });
  }
  
  setupEvents() {
    // Play/pause button
    this.playButton.addEventListener('click', () => {
      this.togglePlayPause();
    });
    
    // Wavesurfer events
    this.wavesurfer.on('ready', () => {
      this.hideLoading();
      this.updateTimeDisplay();
    });
    
    this.wavesurfer.on('play', () => {
      this.isPlaying = true;
      this.updatePlayButton();
    });
    
    this.wavesurfer.on('pause', () => {
      this.isPlaying = false;
      this.updatePlayButton();
    });
    
    this.wavesurfer.on('finish', () => {
      this.isPlaying = false;
      this.updatePlayButton();
    });
    
    this.wavesurfer.on('audioprocess', () => {
      this.updateTimeDisplay();
    });
    
    this.wavesurfer.on('seek', () => {
      this.updateTimeDisplay();
    });
    
    this.wavesurfer.on('loading', (percent) => {
      this.showLoading(percent);
    });
    
    this.wavesurfer.on('error', (err) => {
      console.error('WaveSurfer error:', err);
      this.hideLoading();
    });
  }
  
  loadAudio(url) {
    this.showLoading();
    this.wavesurfer.load(url);
  }
  
  play() {
    this.wavesurfer.play();
  }
  
  pause() {
    this.wavesurfer.pause();
  }
  
  togglePlayPause() {
    this.wavesurfer.playPause();
  }
  
  stop() {
    this.wavesurfer.stop();
  }
  
  updatePlayButton() {
    const playIcon = this.playButton.querySelector('.play-icon');
    const pauseIcon = this.playButton.querySelector('.pause-icon');
    
    if (this.isPlaying) {
      playIcon.style.display = 'none';
      pauseIcon.style.display = 'block';
    } else {
      playIcon.style.display = 'block';
      pauseIcon.style.display = 'none';
    }
  }
  
  showLoading(percent) {
    this.loadingIndicator.style.display = 'flex';
    if (percent !== undefined) {
      this.loadingIndicator.querySelector('span').textContent = `Loading: ${Math.round(percent)}%`;
    }
  }
  
  hideLoading() {
    this.loadingIndicator.style.display = 'none';
  }
  
  formatTime(seconds) {
    const minutes = Math.floor(seconds / 60);
    const secondsRemainder = Math.round(seconds) % 60;
    const paddedSeconds = secondsRemainder.toString().padStart(2, '0');
    return `${minutes}:${paddedSeconds}`;
  }
  
  updateTimeDisplay() {
    if (!this.wavesurfer.isReady) return;
    
    const currentTime = this.formatTime(this.wavesurfer.getCurrentTime());
    const duration = this.formatTime(this.wavesurfer.getDuration());
    this.timeDisplay.textContent = `${currentTime} / ${duration}`;
  }
}

// Allow global access
window.WavePlayer = WavePlayer; 