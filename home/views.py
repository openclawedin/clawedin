from django.shortcuts import render


def landing(request):
    return render(request, "home/index.html")


def privacy(request):
    return render(request, "home/privacy.html")


def terms(request):
    return render(request, "home/terms.html")
