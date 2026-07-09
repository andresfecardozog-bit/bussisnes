import {
  AfterViewInit,
  Directive,
  ElementRef,
  NgZone,
  OnDestroy,
  inject,
  input,
} from '@angular/core';

@Directive({
  selector: '[appCountUp]',
  standalone: true,
})
export class CountUpDirective implements AfterViewInit, OnDestroy {
  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly zone = inject(NgZone);
  private observer?: IntersectionObserver;
  private animationFrame?: number;

  readonly target = input(0, { alias: 'appCountUp' });
  readonly durationMs = input(1200);
  readonly decimals = input(0);
  readonly prefix = input('');
  readonly suffix = input('');

  ngAfterViewInit(): void {
    this.observer = new IntersectionObserver(
      entries => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) {
            return;
          }
          this.startAnimation();
          this.observer?.disconnect();
        });
      },
      { threshold: 0.4 },
    );
    this.observer.observe(this.host.nativeElement);
  }

  ngOnDestroy(): void {
    this.observer?.disconnect();
    if (this.animationFrame !== undefined) {
      cancelAnimationFrame(this.animationFrame);
    }
  }

  private startAnimation(): void {
    const duration = Math.max(this.durationMs(), 300);
    const target = Number(this.target()) || 0;
    const decimals = Math.max(this.decimals(), 0);
    const formatter = new Intl.NumberFormat('es-CO', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    const start = performance.now();

    this.zone.runOutsideAngular(() => {
      const tick = (now: number): void => {
        const progress = Math.min((now - start) / duration, 1);
        const eased = 1 - (1 - progress) * (1 - progress) * (1 - progress);
        const value = target * eased;
        this.host.nativeElement.textContent =
          `${this.prefix()}${formatter.format(value)}${this.suffix()}`;

        if (progress < 1) {
          this.animationFrame = requestAnimationFrame(tick);
        }
      };
      this.animationFrame = requestAnimationFrame(tick);
    });
  }
}
