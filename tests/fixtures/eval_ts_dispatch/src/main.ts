class Base {
  protected base(): number { return 1; }
}

class Child extends Base {
  run(): number { return this.helper(); }
  helper(): number { return 2; }
  work(): number { return super.base() + this.helper() + this.base(); }
}

export function entry(): number {
  return new Child().work();
}
