import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { BrandMarkComponent } from '../../shared/brand-mark.component';
import { CountUpDirective } from '../../shared/directives/count-up.directive';
import { RevealOnScrollDirective } from '../../shared/directives/reveal-on-scroll.directive';

@Component({
  selector: 'app-landing',
  standalone: true,
  imports: [RouterLink, BrandMarkComponent, CountUpDirective, RevealOnScrollDirective],
  templateUrl: './landing.component.html',
  styleUrl: './landing.component.scss',
})
export class LandingComponent {}
