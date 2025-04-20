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
    
    // Add style to hide native audio elements that might be rendered by wavesurfer
    const style = document.createElement('style');
    style.textContent = `
      .waveplayer audio {
        display: none !important;
      }
      
      /* Mobile optimizations */
      @media (max-width: 768px) {
        .waveplayer-play-btn {
          width: 44px;
          height: 44px;
          margin-right: 12px;
        }
        
        .waveplayer-waveform {
          height: 70px;
          cursor: pointer;
          touch-action: none; /* Prevents scroll/zoom on touch */
        }
      }
    `;
    this.container.appendChild(style);
    
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
    
    // Set up MutationObserver to detect when loading reaches 100%
    const loadingTextElement = this.loadingIndicator.querySelector('span');
    if (loadingTextElement) {
      const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
          if (mutation.type === 'characterData' || mutation.type === 'childList') {
            const text = loadingTextElement.textContent;
            if (text && text.includes('100%')) {
              // If we see "100%", hide the loading indicator after a short delay
              setTimeout(() => this.hideLoading(), 300);
            }
          }
        });
      });
      
      observer.observe(loadingTextElement, { 
        characterData: true, 
        childList: true,
        subtree: true
      });
    }
    
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
      ...this.options,
      // Add mobile touch support
      interact: true,
      dragToSeek: true
    });
    
    // Force reset any loading indicators
    if (this.loadingIndicator) {
      this.loadingIndicator.style.display = 'none';
    }
  }
  
  setupEvents() {
    // Play/pause button
    this.playButton.addEventListener('click', () => {
      this.togglePlayPause();
    });
    
    // Add touch support for mobile
    this.playButton.addEventListener('touchstart', (e) => {
      e.preventDefault();
      this.togglePlayPause();
    });
    
    // Add touch support for waveform container
    this.waveformContainer.addEventListener('touchstart', (e) => {
      // This helps ensure the touch events propagate correctly to wavesurfer
      e.stopPropagation();
    });
    
    // Wavesurfer events
    this.wavesurfer.on('ready', () => {
      // Clear loading timeout
      if (this.loadingTimeout) {
        clearTimeout(this.loadingTimeout);
      }
      
      // Explicitly ensure loading indicator is hidden
      this.hideLoading();
      this.updateTimeDisplay();
      
      // Force loading message to be reset
      if (this.loadingIndicator && this.loadingIndicator.querySelector('span')) {
        this.loadingIndicator.querySelector('span').textContent = 'Loading...';
      }
      
      console.log('WavePlayer ready event fired');
    });
    
    // Add specific handler for decode event (fired when audio is decoded)
    this.wavesurfer.on('decode', () => {
      // Also hide loading indicator after decode
      this.hideLoading();
      console.log('WavePlayer decode event fired');
    });
    
    // Add specific handler for loading complete
    this.wavesurfer.on('loading', (percent) => {
      this.showLoading(percent);
      
      // If loading reaches 100%, make sure to hide the loader after a small delay
      if (percent === 100) {
        setTimeout(() => {
          this.hideLoading();
          console.log('WavePlayer loading 100% - force hiding loader');
        }, 500);
      }
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
    
    this.wavesurfer.on('error', (err) => {
      console.error('WaveSurfer error:', err);
      this.hideLoading();
    });
  }
  
  loadAudio(url) {
    this.showLoading();
    this.wavesurfer.load(url);
    
    // Safety timeout to ensure loading indicator gets hidden
    // even if the 'ready' event doesn't fire properly
    this.loadingTimeout = setTimeout(() => {
      this.hideLoading();
    }, 10000); // 10 seconds max loading time
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
    if (this.loadingIndicator) {
      this.loadingIndicator.style.display = 'none';
      
      // Reset loading text
      const loadingText = this.loadingIndicator.querySelector('span');
      if (loadingText) {
        loadingText.textContent = 'Loading...';
      }
    }
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