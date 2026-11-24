/* ==========================================================================
   KOLInsights - Interactive JavaScript Controller
   ========================================================================== */

document.addEventListener('DOMContentLoaded', () => {
  // 1. Interactive Documentation Tab Switching
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabContents = document.querySelectorAll('.tab-content');

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const tabId = btn.getAttribute('data-tab');

      tabBtns.forEach(b => b.classList.remove('active'));
      tabContents.forEach(c => c.classList.remove('active'));

      btn.classList.add('active');
      const targetContent = document.getElementById(tabId);
      if (targetContent) {
        targetContent.classList.add('active');
      }
    });
  });

  // 2. 1-Tap Copy Deposit Address
  const copyBtn = document.getElementById('copyWalletBtn');
  const walletAddr = document.getElementById('depositWalletAddr');

  if (copyBtn && walletAddr) {
    copyBtn.addEventListener('click', () => {
      const textToCopy = walletAddr.textContent.trim();
      navigator.clipboard.writeText(textToCopy).then(() => {
        const originalText = copyBtn.textContent;
        copyBtn.textContent = '✓ Copied!';
        copyBtn.style.background = '#34d399';

        setTimeout(() => {
          copyBtn.textContent = originalText;
          copyBtn.style.background = 'var(--accent-emerald)';
        }, 2000);
      }).catch(err => {
        console.error('Failed to copy: ', err);
      });
    });
  }

  // 3. Smooth Navbar Glass Blur on Scroll & Mobile Menu Toggle
  const navbar = document.querySelector('.navbar');
  const mobileMenuBtn = document.getElementById('mobileMenuBtn');
  const navMenu = document.getElementById('nav-menu');

  if (mobileMenuBtn && navMenu) {
    mobileMenuBtn.addEventListener('click', () => {
      mobileMenuBtn.classList.toggle('active');
      navMenu.classList.toggle('mobile-open');
    });

    // Close mobile menu when clicking a link
    document.querySelectorAll('.nav-link').forEach(link => {
      link.addEventListener('click', () => {
        mobileMenuBtn.classList.remove('active');
        navMenu.classList.remove('mobile-open');
      });
    });
  }

  window.addEventListener('scroll', () => {
    if (window.scrollY > 50) {
      navbar.style.background = 'rgba(8, 10, 15, 0.95)';
      navbar.style.boxShadow = '0 10px 30px rgba(0, 0, 0, 0.5)';
    } else {
      navbar.style.background = 'rgba(8, 10, 15, 0.85)';
      navbar.style.boxShadow = 'none';
    }
  });
});
