import {
  AfterViewInit,
  Directive,
  ElementRef,
  OnDestroy,
  Renderer2,
  inject,
  input,
} from '@angular/core';

@Directive({
  selector: '[appRevealOnScroll]',
  standalone: true,
})
export class RevealOnScrollDirective implements AfterViewInit, OnDestroy {
  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly renderer = inject(Renderer2);
  private observer?: IntersectionObserver;

  readonly revealDelay = input(0);

  constructor() {
    const element = this.host.nativeElement;
    this.renderer.setStyle(element, 'opacity', '0');
    this.renderer.setStyle(element, 'transform', 'translateY(18px)');
    this.renderer.setStyle(
      element,
      'transition',
      'opacity 450ms cubic-bezier(0.2, 0, 0, 1), transform 450ms cubic-bezier(0.2, 0, 0, 1)',
    );
    this.renderer.setStyle(element, 'will-change', 'opacity, transform');
  }

  ngAfterViewInit(): void {
    this.observer = new IntersectionObserver(
      entries => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) {
            return;
          }
          this.renderer.setStyle(
            this.host.nativeElement,
            'transition-delay',
            `${this.revealDelay()}ms`,
          );
          this.renderer.setStyle(this.host.nativeElement, 'opacity', '1');
          this.renderer.setStyle(this.host.nativeElement, 'transform', 'translateY(0)');
          this.observer?.disconnect();
        });
      },
      { threshold: 0.16 },
    );
    this.observer.observe(this.host.nativeElement);
  }

  ngOnDestroy(): void {
    this.observer?.disconnect();
  }
}
