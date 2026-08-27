require 'json'

package = JSON.parse(File.read(File.join(__dir__, 'package.json')))

Pod::Spec.new do |s|
  s.name = 'LgSsap'
  s.version = package['version']
  s.summary = package['description']
  s.license = 'MIT'
  s.homepage = 'https://github.com/eduardoperorck/lg-remote'
  s.author = 'lg-remote'
  s.source = { :git => 'https://github.com/eduardoperorck/lg-remote', :tag => s.version.to_s }
  s.source_files = 'ios/Sources/**/*.{swift,h,m,c,cc,mm,cpp}'
  # Tem que ser o mesmo piso do Capacitor.podspec (14.0). Pedir mais que o
  # framework em que o plugin se encaixa faz o CocoaPods recusar a resolução
  # inteira com "required a higher minimum deployment target" — e nada aqui
  # precisa de mais: só há async/await, que retrocompila até o iOS 13.
  s.ios.deployment_target = '14.0'
  s.dependency 'Capacitor'
  s.swift_version = '5.9'
end
