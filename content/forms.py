from django import forms

from .models import Comment, Post


class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = ("title", "body")
        widgets = {
            "body": forms.Textarea(attrs={"rows": 8}),
        }


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ("body",)
        widgets = {
            "body": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Write a comment..."},
            ),
        }
